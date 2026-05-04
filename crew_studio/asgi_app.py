"""
ASGI entry point — FastAPI application that replaces the Flask dev server.

Strategy:
  - Critical hot-path routes (health, jobs CRUD) are native async FastAPI.
  - Everything else is delegated to the existing Flask app via WSGIMiddleware.
  - Long-running job execution is dispatched with asyncio.to_thread so it
    never blocks the event loop.
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from crew_studio.job_database import JobDatabase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database & workspace setup (mirrors Flask app's init logic)
# ---------------------------------------------------------------------------
db_path = Path(os.getenv("JOB_DB_PATH", "./crew_jobs.db"))
job_db = JobDatabase(db_path)

base_workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
base_workspace_path.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Config — loaded lazily so tests can patch before first use
# ---------------------------------------------------------------------------
_config = None


def _get_config():
    global _config
    if _config is None:
        try:
            from src.llamaindex_crew.config import ConfigLoader
            _config = ConfigLoader.load()
        except Exception as e:
            logger.warning("Failed to load config: %s", e)
    return _config


# ---------------------------------------------------------------------------
# Job runner — imported lazily to avoid heavy imports at module level
# ---------------------------------------------------------------------------

def _run_job_sync(job_id: str, vision: str, config_obj):
    """Synchronous wrapper that runs in a thread via asyncio.to_thread."""
    from crew_studio.llamaindex_web_app import run_job_async
    run_job_async(job_id, vision, config_obj)


def _run_job_with_backend_sync(job_id: str, vision: str, backend):
    from crew_studio.llamaindex_web_app import run_job_with_backend
    run_job_with_backend(job_id, vision, backend)


# ---------------------------------------------------------------------------
# CORS — derive allowed origins from environment for split frontend deployment
# ---------------------------------------------------------------------------

def _cors_origins() -> list[str]:
    """Build the allowed-origins list from CORS_ALLOWED_ORIGINS env var.

    Comma-separated list. Defaults to common local dev origins when unset.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173"]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """On startup: resume any jobs that were in-flight when the server last stopped."""
    skip = os.getenv("SKIP_STARTUP_RESUME", "").strip().lower() in ("1", "true", "yes")
    if not skip:
        try:
            from crew_studio.llamaindex_web_app import resume_pending_jobs
            await asyncio.to_thread(resume_pending_jobs)
            logger.info("Startup: resume_pending_jobs complete")
        except Exception:
            logger.exception("Startup: resume_pending_jobs failed (non-fatal)")
    yield


app = FastAPI(title="AI Software Development Crew", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health endpoints — native async, zero blocking
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "AI Software Development Crew",
            "version": "2.0.0", "timestamp": datetime.now().isoformat()}


@app.get("/health/live")
async def health_live():
    return {"status": "alive", "timestamp": datetime.now().isoformat()}


# ---------------------------------------------------------------------------
# Job CRUD — native async, DB calls are sync but fast (SQLite)
# ---------------------------------------------------------------------------

class CreateJobRequest(BaseModel):
    vision: str
    backend: str = "opl-ai-team"
    github_urls: List[str] = []
    mode: str = "build"
    metadata: Dict[str, Any] = {}


def _resolve_backend(name: str):
    """Look up the backend, returning None for the default OPL path."""
    try:
        from src.llamaindex_crew.backends import registry
        backend = registry.get_backend(name)
        if not backend:
            return None, f"Unknown backend: {name}"
        if not backend.is_available():
            return None, f"Backend not available: {name}"
        return backend, None
    except ImportError:
        return None, None if name == "opl-ai-team" else f"Backend not available: {name}"
    except Exception as e:
        logger.warning("Backend registry error: %s", e)
        return None, None if name == "opl-ai-team" else f"Backend not available: {name}"


def _dispatch_job(job_id: str, vision: str, backend_name: str):
    """Runs in executor thread — safe to do slow imports/work here."""
    config_obj = _get_config()
    if backend_name != "opl-ai-team":
        backend, _ = _resolve_backend(backend_name)
        if backend:
            _run_job_with_backend_sync(job_id, vision, backend)
            return
    _run_job_sync(job_id, vision, config_obj)


@app.post("/api/jobs")
async def create_job(request: Request):
    """Create a new job. JSON for greenfield; multipart is delegated to Flask (ZIP, import, migration, refactor)."""

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        body_bytes = await request.body()

        def _forward_multipart():
            from crew_studio.llamaindex_web_app import app as flask_app
            with flask_app.test_client() as client:
                return client.post(
                    "/api/jobs",
                    data=body_bytes,
                    content_type=content_type,
                )

        try:
            resp = await asyncio.to_thread(_forward_multipart)
        except Exception:
            logger.exception("Multipart POST /api/jobs forward to Flask failed")
            raise HTTPException(
                status_code=502,
                detail="Multipart job create failed (see server logs).",
            ) from None
        out_ct = resp.headers.get("Content-Type") or "application/json"
        media = out_ct.split(";")[0].strip()
        payload = getattr(resp, "data", None) or resp.get_data()
        return Response(
            content=payload,
            status_code=resp.status_code,
            media_type=media,
        )

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    try:
        body = CreateJobRequest.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not body.vision:
        raise HTTPException(status_code=400, detail="Vision is required")

    if body.backend != "opl-ai-team":
        _, err = _resolve_backend(body.backend)
        if err:
            raise HTTPException(status_code=400, detail=err)

    job_id = str(uuid.uuid4())
    job_workspace = base_workspace_path / f"job-{job_id}"
    job_workspace.mkdir(parents=True, exist_ok=True)

    job_db.create_job(job_id, body.vision, str(job_workspace), metadata=body.metadata)

    if body.mode in ("migration", "refactor", "import"):
        if body.mode == "import":
            meta = dict(body.metadata) if body.metadata else {}
            meta["job_mode"] = "import"
            job_db.update_job(job_id, {"metadata": json.dumps(meta)})
        phase = (
            "awaiting_migration" if body.mode == "migration"
            else "awaiting_refactor" if body.mode == "refactor"
            else "awaiting_import"
        )
        job_db.update_job(job_id, {"status": "queued", "current_phase": phase})
        return JSONResponse(
            status_code=201,
            content={
                "job_id": job_id,
                "status": "queued",
                "documents": 0,
                "source_files": 0,
                "github_repos": 0,
            },
        )

    # Tests / tooling: avoid spawning the full LLM pipeline from the request handler.
    if os.getenv("CREW_TEST_NO_EXECUTOR", "").strip().lower() not in ("1", "true", "yes"):
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _dispatch_job, job_id, body.vision, body.backend)

    return JSONResponse(
        status_code=201,
        content={
            "job_id": job_id,
            "status": "queued",
            "documents": 0,
            "github_repos": 0,
        },
    )


@app.get("/api/jobs")
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    vision_contains: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
):
    offset = (page - 1) * page_size
    total = job_db.get_jobs_count(
        vision_filter=vision_contains, status_filter=status)
    jobs = job_db.get_jobs_paginated(
        limit=page_size, offset=offset,
        vision_filter=vision_contains, status_filter=status,
        sort_by=sort_by, sort_order=sort_order,
    )

    def _summary(job):
        summary = {
            "id": job["id"], "vision": job["vision"],
            "status": job["status"], "progress": job["progress"],
            "current_phase": job["current_phase"],
            "created_at": job["created_at"],
            "completed_at": job.get("completed_at"),
        }
        if job.get("metadata"):
            summary["metadata"] = job["metadata"]
        return summary

    return {"jobs": [_summary(j) for j in jobs],
            "total": total, "page": page, "page_size": page_size}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = job_db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/progress")
async def get_job_progress(job_id: str):
    job = job_db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    last_messages = job.get("last_message", [])
    if isinstance(last_messages, str):
        try:
            last_messages = json.loads(last_messages)
        except (json.JSONDecodeError, TypeError):
            last_messages = []
    return {
        "status": job["status"],
        "progress": job["progress"],
        "current_phase": job["current_phase"],
        "last_message": last_messages[-10:],
    }


@app.get("/api/backends")
async def list_backends():
    try:
        from src.llamaindex_crew.backends import registry
        backends = registry.list_backends()
        return {"backends": backends}
    except Exception:
        return {"backends": [
            {"name": "opl-ai-team", "display_name": "OPL AI Team", "available": True}
        ]}


@app.get("/api/stats")
async def stats():
    total = job_db.get_jobs_count()
    running = job_db.get_jobs_count(status_filter="running")
    completed = job_db.get_jobs_count(status_filter="completed")
    failed = job_db.get_jobs_count(status_filter="failed")
    return {"total": total, "running": running,
            "completed": completed, "failed": failed}


# ---------------------------------------------------------------------------
# Skills proxy — forwards to the skills-service if available
# ---------------------------------------------------------------------------

SKILLS_SERVICE_URL = os.getenv("SKILLS_SERVICE_URL", "").rstrip("/")


class SkillQueryRequest(BaseModel):
    query: str
    top_k: int = 5
    tags: Optional[List[str]] = None


@app.get("/api/skills")
async def list_skills():
    """List all available skills. Returns empty list if skills service is down."""
    if not SKILLS_SERVICE_URL:
        return {"skills": [], "available": False}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{SKILLS_SERVICE_URL}/skills")
            resp.raise_for_status()
            data = resp.json()
            return {**data, "available": True}
    except Exception:
        logger.debug("Skills service unreachable at %s", SKILLS_SERVICE_URL)
        return {"skills": [], "available": False}


@app.post("/api/skills/query")
async def query_skills(body: SkillQueryRequest):
    """Semantic search over skills. Returns empty results if service is down."""
    if not SKILLS_SERVICE_URL:
        return {"results": [], "available": False}
    try:
        payload: dict = {"query": body.query, "top_k": body.top_k}
        if body.tags:
            payload["tags"] = body.tags
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{SKILLS_SERVICE_URL}/query", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            return {**data, "available": True}
    except Exception:
        logger.debug("Skills query failed against %s", SKILLS_SERVICE_URL)
        return {"results": [], "available": False}


@app.post("/api/skills/reload", status_code=202)
async def reload_skills():
    """Trigger a skills index rebuild. 503 if service is unreachable."""
    if not SKILLS_SERVICE_URL:
        raise HTTPException(status_code=503, detail="Skills service not configured")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{SKILLS_SERVICE_URL}/reload")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        raise HTTPException(status_code=503, detail="Skills service unreachable")


# ---------------------------------------------------------------------------
# Mount the existing Flask app for all remaining routes
# ---------------------------------------------------------------------------

def mount_flask_fallback():
    """Mount the existing Flask WSGI app for routes not yet ported to FastAPI.

    Call this explicitly in production startup (e.g. dev-backend.sh).
    Skipped automatically during tests or when the Flask app isn't available.
    """
    try:
        from starlette.middleware.wsgi import WSGIMiddleware
        from crew_studio.llamaindex_web_app import app as flask_app
        app.mount("/", WSGIMiddleware(flask_app))
        logger.info("Flask WSGI app mounted as fallback for unported routes")
    except Exception as e:
        logger.warning("Flask WSGI mount skipped: %s", e)


if os.getenv("MOUNT_FLASK_FALLBACK", "").lower() in ("1", "true", "yes"):
    mount_flask_fallback()
