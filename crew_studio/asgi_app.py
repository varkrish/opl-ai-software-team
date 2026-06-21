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
from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from crew_studio.job_database import JobDatabase
from crew_studio.auth import get_current_user, CurrentUser, decode_and_verify_token

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

def _run_job_sync(job_id: str, vision: str, config_obj, resume: bool = False):
    """Synchronous wrapper that runs in a thread via asyncio.to_thread."""
    from crew_studio.llamaindex_web_app import run_job_async
    run_job_async(job_id, vision, config_obj, resume=resume)


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
# Authentication Middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    path = request.url.path

    # Secure all /api/* endpoints
    if path.startswith("/api/"):
        from crew_studio.auth import AUTH_ENABLED, MOCK_USER

        if not AUTH_ENABLED:
            user = MOCK_USER
        else:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Missing or invalid authorization credentials"}
                )

            token = auth_header.split(" ")[1]
            try:
                user = decode_and_verify_token(token)
            except Exception as e:
                print(f"DEBUG: Token validation failed: {type(e).__name__}: {str(e)}")
                logger.error(f"Token validation failed: {e}")
                return JSONResponse(
                    status_code=401,
                    content={"detail": f"Invalid authentication token: {str(e)}"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

        # Store user in request state for FastAPI path handlers
        request.state.user = user

        # Inject headers into ASGI scope for WSGI/Flask fallback
        headers = list(request.scope["headers"])
        headers.append((b"x-user-id", str(user.user_id or "unknown").encode("utf-8")))
        headers.append((b"x-user-email", str(user.email or "unknown@example.com").encode("utf-8")))
        headers.append((b"x-user-roles", ",".join(user.roles).encode("utf-8")))
        headers.append((b"x-user-teams", ",".join(user.teams).encode("utf-8")))
        headers.append((b"x-user-admin", str(user.is_admin).lower().encode("utf-8")))
        request.scope["headers"] = headers

    response = await call_next(request)
    return response


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
    team_id: Optional[str] = None
    github_urls: List[str] = []
    mode: str = "build"
    metadata: Dict[str, Any] = {}
    auto_approve_plan: bool = False  # when True, skip the plan review gate for this job
    jira_issue_key: Optional[str] = None   # e.g. "PROJ-123"
    jira_issue_url: Optional[str] = None   # full browse URL
    jira_issue_summary: Optional[str] = None  # issue title for display


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


def _dispatch_job(job_id: str, vision: str, backend_name: str, resume: bool = False):
    """Runs in executor thread — safe to do slow imports/work here."""
    config_obj = _get_config()
    if backend_name != "opl-ai-team":
        backend, _ = _resolve_backend(backend_name)
        if backend:
            _run_job_with_backend_sync(job_id, vision, backend)
            return
    _run_job_sync(job_id, vision, config_obj, resume=resume)


def _build_forward_headers(request: Request) -> dict:
    """Build headers dict for Flask forwarding, including injected auth headers."""
    forward_headers = {}
    for k, v in request.headers.items():
        forward_headers[k] = v
    if hasattr(request.state, "user"):
        u = request.state.user
        forward_headers["x-user-id"] = u.user_id
        forward_headers["x-user-email"] = u.email
        forward_headers["x-user-roles"] = ",".join(u.roles)
        forward_headers["x-user-teams"] = ",".join(u.teams)
        forward_headers["x-user-admin"] = str(u.is_admin).lower()
    return forward_headers


@app.post("/api/jobs")
async def create_job(request: Request, user: CurrentUser = Depends(get_current_user)):
    """Create a new job. JSON for greenfield; multipart is delegated to Flask (ZIP, import, migration, refactor)."""

    content_type = request.headers.get("content-type", "")
    forward_headers = _build_forward_headers(request)

    if "multipart/form-data" in content_type:
        body_bytes = await request.body()

        def _forward_multipart():
            from crew_studio.llamaindex_web_app import app as flask_app
            with flask_app.test_client() as client:
                return client.post(
                    "/api/jobs",
                    data=body_bytes,
                    content_type=content_type,
                    headers=forward_headers,
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

    # Import/fix with GitHub URLs need Flask handler (clone into workspace)
    if body.mode in ("import", "fix") and body.github_urls:
        def _forward_json():
            from crew_studio.llamaindex_web_app import app as flask_app
            with flask_app.test_client() as client:
                return client.post(
                    "/api/jobs",
                    json=payload,
                    content_type="application/json",
                    headers=forward_headers,
                )

        try:
            resp = await asyncio.to_thread(_forward_json)
        except Exception:
            logger.exception("JSON POST /api/jobs forward to Flask failed")
            raise HTTPException(
                status_code=502,
                detail="Job create failed (see server logs).",
            ) from None
        out_ct = resp.headers.get("Content-Type") or "application/json"
        media = out_ct.split(";")[0].strip()
        data = getattr(resp, "data", None) or resp.get_data()
        return Response(
            content=data,
            status_code=resp.status_code,
            media_type=media,
        )

    if not body.vision:
        if body.mode in ("import", "fix"):
            pass  # default vision applied below
        else:
            raise HTTPException(status_code=400, detail="Vision is required")

    if body.backend != "opl-ai-team":
        _, err = _resolve_backend(body.backend)
        if err:
            raise HTTPException(status_code=400, detail=err)

    if body.team_id:
        team_to_check = body.team_id.lstrip("/")
        if not user.is_admin and team_to_check not in user.teams:
            raise HTTPException(
                status_code=403,
                detail=f"User is not a member of team '{body.team_id}'"
            )

    job_id = str(uuid.uuid4())
    job_workspace = base_workspace_path / f"job-{job_id}"
    job_workspace.mkdir(parents=True, exist_ok=True)

    meta = dict(body.metadata) if body.metadata else {}
    meta["auto_approve_plan"] = bool(body.auto_approve_plan)
    if body.jira_issue_key:
        meta["jira_issue_key"] = body.jira_issue_key
    if body.jira_issue_url:
        meta["jira_issue_url"] = body.jira_issue_url
    if body.jira_issue_summary:
        meta["jira_issue_summary"] = body.jira_issue_summary

    effective_mode = body.mode
    if body.mode == "fix":
        from crew_studio.work_intent import apply_fix_mode_metadata
        auto_fix = meta.get("auto_fix_after_analyze", True)
        meta = apply_fix_mode_metadata(meta, auto_fix=bool(auto_fix))
        effective_mode = "import"

    vision = body.vision or "[Import] Existing codebase"
    job_db.create_job(
        job_id,
        vision,
        str(job_workspace),
        metadata=meta,
        owner_id=user.user_id,
        owner_email=user.email,
        team_id=body.team_id,
    )

    if effective_mode in ("migration", "refactor", "import"):
        if effective_mode == "import":
            meta["job_mode"] = "import"
            job_db.update_job(job_id, {"metadata": json.dumps(meta)})
        phase = (
            "awaiting_migration" if effective_mode == "migration"
            else "awaiting_refactor" if effective_mode == "refactor"
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
                "mode": body.mode,
            },
        )

    # Tests / tooling: avoid spawning the full LLM pipeline from the request handler.
    if os.getenv("CREW_TEST_NO_EXECUTOR", "").strip().lower() not in ("1", "true", "yes"):
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _dispatch_job, job_id, vision, body.backend)

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
    team_id: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
):
    offset = (page - 1) * page_size
    total = job_db.get_jobs_count(
        vision_filter=vision_contains, status_filter=status,
        owner_id=user.user_id, team_ids=user.teams, is_admin=user.is_admin,
        team_id=team_id
    )
    jobs = job_db.get_jobs_paginated(
        limit=page_size, offset=offset,
        vision_filter=vision_contains, status_filter=status,
        sort_by=sort_by, sort_order=sort_order,
        owner_id=user.user_id, team_ids=user.teams, is_admin=user.is_admin,
        team_id=team_id
    )

    def _summary(job):
        summary = {
            "id": job["id"], "vision": job["vision"],
            "status": job["status"], "progress": job["progress"],
            "current_phase": job["current_phase"],
            "created_at": job["created_at"],
            "completed_at": job.get("completed_at"),
            "owner_id": job.get("owner_id"),
            "owner_email": job.get("owner_email"),
            "team_id": job.get("team_id"),
        }
        if job.get("metadata"):
            summary["metadata"] = job["metadata"]
        if "cost" in job:
            summary["cost"] = job["cost"]
        if "tokens" in job:
            summary["tokens"] = job["tokens"]
        return summary

    return {"jobs": [_summary(j) for j in jobs],
            "total": total, "page": page, "page_size": page_size}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: CurrentUser = Depends(get_current_user)):
    job = job_db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not user.is_admin:
        has_access = (
            (job.get("owner_id") == user.user_id) or
            (job.get("team_id") and job.get("team_id").lstrip("/") in user.teams)
        )
        if not has_access:
            raise HTTPException(status_code=404, detail="Job not found")

    return job


@app.get("/api/jobs/{job_id}/progress")
async def get_job_progress(job_id: str, user: CurrentUser = Depends(get_current_user)):
    job = job_db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not user.is_admin:
        has_access = (
            (job.get("owner_id") == user.user_id) or
            (job.get("team_id") and job.get("team_id").lstrip("/") in user.teams)
        )
        if not has_access:
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


@app.post("/api/jobs/{job_id}/approve")
async def approve_job(job_id: str, user: CurrentUser = Depends(get_current_user)):
    """Resume a pending_approval or pending_review job."""
    job = job_db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not user.is_admin:
        has_access = (
            (job.get("owner_id") == user.user_id) or
            (job.get("team_id") and job.get("team_id").lstrip("/") in user.teams)
        )
        if not has_access:
            raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") not in ("pending_approval", "pending_review"):
        raise HTTPException(
            status_code=400,
            detail=f"Job is not pending approval/review (status={job.get('status')})",
        )
    raw_meta = job.get("metadata") or {}
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    meta["pending_review_approved"] = True
    job_db.update_job(job_id, {
        "status": "queued",
        "current_phase": "development",
        "metadata": json.dumps(meta),
    })
    if os.getenv("CREW_TEST_NO_EXECUTOR", "").strip().lower() not in ("1", "true", "yes"):
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None,
            _dispatch_job,
            job_id,
            job.get("vision", ""),
            "opl-ai-team",
            True,
        )
    return {"job_id": job_id, "status": "resumed"}


class RefinePlanRequest(BaseModel):
    feedback: str


@app.post("/api/jobs/{job_id}/refine-plan")
async def refine_plan(job_id: str, body: RefinePlanRequest, user: CurrentUser = Depends(get_current_user)):
    """Re-run planning phases with user feedback while job is pending_review/pending_approval."""
    job = job_db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not user.is_admin:
        has_access = (
            (job.get("owner_id") == user.user_id) or
            (job.get("team_id") and job.get("team_id").lstrip("/") in user.teams)
        )
        if not has_access:
            raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") not in ("pending_review", "pending_approval"):
        raise HTTPException(
            status_code=400,
            detail=f"Job is not in a reviewable state (status={job.get('status')})",
        )
    if not body.feedback or not body.feedback.strip():
        raise HTTPException(status_code=400, detail="feedback is required")

    if os.getenv("CREW_TEST_NO_EXECUTOR", "").strip().lower() in ("1", "true", "yes"):
        return {"job_id": job_id, "status": "pending_review", "artifacts": {}, "feedback_rounds": 0}

    def _run_refine():
        from pathlib import Path as _Path
        from src.llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow
        from src.llamaindex_crew.tools.file_tools import set_thread_workspace
        from src.llamaindex_crew.utils.llm_config import user_llm_context

        workspace = _Path(job["workspace_path"])
        set_thread_workspace(str(workspace))
        cfg = _get_config()
        with user_llm_context(job_id, job_db, cfg):
            workflow = SoftwareDevWorkflow(
                project_id=job_id,
                workspace_path=workspace,
                vision=job.get("vision", ""),
                config=cfg,
                progress_callback=lambda phase, prog, msg=None: job_db.update_progress(job_id, phase, prog, msg),
                job_db=job_db,
            )
            return workflow.refine_plan(body.feedback.strip())

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run_refine)
    return {**result, "job_id": job_id}


@app.get("/api/jobs/{job_id}/plan")
async def get_job_plan(job_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return planning artifacts for a job (user_stories, design_spec, tech_stack)."""
    job = job_db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not user.is_admin:
        has_access = (
            (job.get("owner_id") == user.user_id) or
            (job.get("team_id") and job.get("team_id").lstrip("/") in user.teams)
        )
        if not has_access:
            raise HTTPException(status_code=404, detail="Job not found")

    from pathlib import Path as _Path
    workspace = _Path(job["workspace_path"])
    artifacts = {}
    for name in ("user_stories.md", "design_spec.md", "tech_stack.md", "implementation_plan.md", "requirements.md"):
        p = workspace / name
        if p.exists():
            try:
                artifacts[name] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    meta = job.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    return {
        "artifacts": artifacts,
        "jira_stories": meta.get("jira_stories", []),
        "epic_judge_reasoning": meta.get("epic_judge_reasoning"),
        "plan_feedback_history": meta.get("plan_feedback_history", []),
    }


@app.post("/api/jobs/{job_id}/refine-epic-stories")
async def refine_epic_stories(job_id: str, body: RefinePlanRequest, user: CurrentUser = Depends(get_current_user)):
    """Alias for /refine-plan — kept for backward compatibility with epic workflows."""
    return await refine_plan(job_id, body, user)


@app.get("/api/backends")
async def list_backends(user: CurrentUser = Depends(get_current_user)):
    try:
        from src.llamaindex_crew.backends import registry
        backends = registry.list_backends()
        return {"backends": backends}
    except Exception:
        return {"backends": [
            {"name": "opl-ai-team", "display_name": "OPL AI Team", "available": True}
        ]}


@app.get("/api/stats")
async def stats(user: CurrentUser = Depends(get_current_user)):
    return job_db.get_stats(owner_id=user.user_id, team_ids=user.teams, is_admin=user.is_admin)


# ---------------------------------------------------------------------------
# Skills proxy — forwards to the skills-service if available
# ---------------------------------------------------------------------------

SKILLS_SERVICE_URL = os.getenv("SKILLS_SERVICE_URL", "").rstrip("/")


class SkillQueryRequest(BaseModel):
    query: str
    top_k: int = 5
    tags: Optional[List[str]] = None


@app.get("/api/skills")
async def list_skills(user: CurrentUser = Depends(get_current_user)):
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
async def query_skills(body: SkillQueryRequest, user: CurrentUser = Depends(get_current_user)):
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
async def reload_skills(user: CurrentUser = Depends(get_current_user)):
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
# Jira configuration endpoints
# ---------------------------------------------------------------------------

class JiraConfigRequest(BaseModel):
    jira_base_url: str
    jira_email: str
    api_token: str


@app.get("/api/jira/config")
async def get_jira_config(user: CurrentUser = Depends(get_current_user)):
    """Return the current user's Jira config (token masked)."""
    cfg = job_db.get_jira_config(user.user_id)
    if not cfg:
        return {"configured": False}
    token = cfg["api_token"]
    masked = ("*" * max(0, len(token) - 4)) + token[-4:] if len(token) >= 4 else "****"
    return {
        "configured": True,
        "jira_base_url": cfg["jira_base_url"],
        "jira_email": cfg["jira_email"],
        "api_token_masked": masked,
        "updated_at": cfg["updated_at"],
    }


@app.post("/api/jira/config", status_code=201)
async def save_jira_config(
    body: JiraConfigRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Save (or update) the current user's Jira credentials, encrypted at rest."""
    if not user.user_id:
        raise HTTPException(status_code=401, detail="Authenticated user has no identity")
    if not body.jira_base_url.startswith("http"):
        raise HTTPException(status_code=422, detail="jira_base_url must be a valid URL")
    if not body.jira_email or "@" not in body.jira_email:
        raise HTTPException(status_code=422, detail="jira_email must be a valid email")
    if not body.api_token:
        raise HTTPException(status_code=422, detail="api_token is required")
    job_db.save_jira_config(
        owner_id=user.user_id,
        jira_base_url=body.jira_base_url.rstrip("/"),
        jira_email=body.jira_email,
        api_token=body.api_token,
    )
    cfg = job_db.get_jira_config(user.user_id)
    if not cfg:
        return {"saved": True, "configured": False}
    token = cfg["api_token"]
    masked = ("*" * max(0, len(token) - 4)) + token[-4:] if len(token) >= 4 else "****"
    return {
        "saved": True,
        "configured": True,
        "jira_base_url": cfg["jira_base_url"],
        "jira_email": cfg["jira_email"],
        "api_token_masked": masked,
        "updated_at": cfg["updated_at"],
    }


@app.delete("/api/jira/config", status_code=200)
async def delete_jira_config(user: CurrentUser = Depends(get_current_user)):
    """Remove the current user's stored Jira credentials."""
    deleted = job_db.delete_jira_config(user.user_id)
    return {"deleted": deleted}


@app.get("/api/jira/search")
async def search_jira_issues(
    q: str = Query(default="", description="Text to search for in Jira issues"),
    project: str = Query(default="", description="Optional project key filter"),
    user: CurrentUser = Depends(get_current_user),
):
    """Search Jira issues using the current user's stored credentials.

    Auto-detects Jira Server (api/2) vs Cloud (api/3/search/jql) like crew_jira_connector.
    """
    from crew_studio.jira_client import build_search_jql, detect_deployment, search_issues as jira_search

    cfg = job_db.get_jira_config(user.user_id)
    if not cfg:
        raise HTTPException(status_code=424, detail="Jira not configured. Go to Settings → Jira to connect.")

    base_url = cfg["jira_base_url"].rstrip("/")
    try:
        deployment = await detect_deployment(base_url, (cfg["jira_email"], cfg["api_token"]))
        jql = build_search_jql(q, project, deployment=deployment)
        return await jira_search(
            base_url,
            cfg["jira_email"],
            cfg["api_token"],
            jql,
            max_results=20,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(status_code=424, detail="Jira credentials invalid — reconnect in Settings → Jira.")
        if e.response.status_code == 400:
            try:
                detail = e.response.json().get("errorMessages", [e.response.text])
            except Exception:
                detail = [e.response.text]
            raise HTTPException(status_code=400, detail=f"Jira query error: {'; '.join(detail)}")
        logger.exception("Jira search HTTP error for user %s: %s", user.user_id, e)
        raise HTTPException(status_code=502, detail=f"Jira search failed: {e}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Jira search failed for user %s: %s", user.user_id, e)
        raise HTTPException(status_code=502, detail=f"Jira search failed: {e}")


@app.post("/api/jira/test-connection")
async def test_jira_connection(
    body: JiraConfigRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Test Jira credentials without saving them.

    Calls GET /rest/api/2/myself on the supplied Jira instance and returns
    the display name on success, or an error message on failure.
    """
    url = body.jira_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url}/rest/api/2/myself",
                auth=(body.jira_email, body.api_token),
                headers={"Accept": "application/json"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "ok": True,
                "display_name": data.get("displayName", body.jira_email),
                "account_id": data.get("accountId"),
            }
        elif resp.status_code == 401:
            return {"ok": False, "error": "Invalid credentials — check your email and API token."}
        elif resp.status_code == 403:
            return {"ok": False, "error": "Forbidden — your account may lack API access."}
        else:
            return {"ok": False, "error": f"Jira returned HTTP {resp.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "error": f"Cannot reach {url} — check the base URL."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GitHub configuration endpoints
# ---------------------------------------------------------------------------

class GitHubConfigRequest(BaseModel):
    api_token: str


@app.get("/api/github/config")
async def get_github_config(user: CurrentUser = Depends(get_current_user)):
    """Return the current user's GitHub config (token masked)."""
    cfg = job_db.get_github_config(user.user_id)
    if not cfg:
        return {"configured": False}
    token = cfg["token"]
    masked = ("*" * max(0, len(token) - 4)) + token[-4:] if len(token) >= 4 else "****"
    return {
        "configured": True,
        "github_username": cfg.get("github_username") or "",
        "api_token_masked": masked,
        "updated_at": cfg["updated_at"],
    }


@app.post("/api/github/config", status_code=201)
async def save_github_config(
    body: GitHubConfigRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Save (or update) the current user's GitHub PAT, encrypted at rest."""
    if not user.user_id:
        raise HTTPException(status_code=401, detail="Authenticated user has no identity")
    if not body.api_token:
        raise HTTPException(status_code=422, detail="api_token is required")

    from crew_studio.github_client import test_github_connection

    try:
        profile = await test_github_connection(body.api_token)
        username = profile.get("login", "")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(status_code=422, detail="Invalid GitHub token")
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub connection failed: {e}")

    job_db.save_github_config(
        owner_id=user.user_id,
        token=body.api_token,
        github_username=username,
    )
    cfg = job_db.get_github_config(user.user_id)
    if not cfg:
        return {"saved": True, "configured": False}
    token = cfg["token"]
    masked = ("*" * max(0, len(token) - 4)) + token[-4:] if len(token) >= 4 else "****"
    return {
        "saved": True,
        "configured": True,
        "github_username": cfg.get("github_username") or username,
        "api_token_masked": masked,
        "updated_at": cfg["updated_at"],
    }


@app.delete("/api/github/config", status_code=200)
async def delete_github_config(user: CurrentUser = Depends(get_current_user)):
    """Remove the current user's stored GitHub credentials."""
    deleted = job_db.delete_github_config(user.user_id)
    return {"deleted": deleted}


@app.get("/api/github/search")
async def search_github_repos(
    q: str = Query(default="", description="Repository name search"),
    user: CurrentUser = Depends(get_current_user),
):
    """Search/list GitHub repositories using the current user's stored PAT."""
    from crew_studio.github_client import search_repositories

    cfg = job_db.get_github_config(user.user_id)
    if not cfg:
        raise HTTPException(
            status_code=424,
            detail="GitHub not configured. Go to Settings → GitHub to connect.",
        )
    try:
        repos = await search_repositories(cfg["token"], q, per_page=20)
        return {"repos": repos, "total": len(repos)}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            raise HTTPException(
                status_code=424,
                detail="GitHub credentials invalid — reconnect in Settings → GitHub.",
            )
        logger.exception("GitHub search HTTP error for user %s: %s", user.user_id, e)
        raise HTTPException(status_code=502, detail=f"GitHub search failed: {e}")
    except Exception as e:
        logger.exception("GitHub search failed for user %s: %s", user.user_id, e)
        raise HTTPException(status_code=502, detail=f"GitHub search failed: {e}")


@app.post("/api/github/test-connection")
async def test_github_connection_endpoint(
    body: GitHubConfigRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Test GitHub PAT without saving it."""
    from crew_studio.github_client import test_github_connection

    if not body.api_token:
        return {"ok": False, "error": "API token is required"}
    try:
        profile = await test_github_connection(body.api_token)
        return {
            "ok": True,
            "login": profile.get("login", ""),
            "name": profile.get("name", ""),
        }
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"ok": False, "error": "Invalid token — check your GitHub PAT."}
        return {"ok": False, "error": f"GitHub returned HTTP {e.response.status_code}"}
    except httpx.ConnectError:
        return {"ok": False, "error": "Cannot reach GitHub API — check your network."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# LLM configuration endpoints
# ---------------------------------------------------------------------------

class LLMConfigRequest(BaseModel):
    api_base_url: str
    api_key: str
    model_manager: Optional[str] = "gpt-4o-mini"
    model_worker: Optional[str] = "gpt-4o-mini"
    model_reviewer: Optional[str] = "gpt-4o-mini"


class LLMConfigResponse(BaseModel):
    configured: bool
    api_base_url: Optional[str] = None
    api_token_masked: Optional[str] = None
    model_manager: Optional[str] = None
    model_worker: Optional[str] = None
    model_reviewer: Optional[str] = None
    updated_at: Optional[str] = None


@app.get("/api/llm/config", response_model=LLMConfigResponse)
async def get_llm_config(user: CurrentUser = Depends(get_current_user)):
    """Return the current user's LLM config (api_key masked)."""
    cfg = job_db.get_llm_config(user.user_id)
    if not cfg:
        return {"configured": False}
    key = cfg["api_key"]
    masked = ("*" * 12) + key[-4:] if len(key) >= 4 else "****"
    return {
        "configured": True,
        "api_base_url": cfg["api_base_url"],
        "api_token_masked": masked,
        "model_manager": cfg["model_manager"],
        "model_worker": cfg["model_worker"],
        "model_reviewer": cfg["model_reviewer"],
        "updated_at": cfg["updated_at"],
    }


@app.post("/api/llm/config", status_code=201)
async def save_llm_config(
    body: LLMConfigRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Save (or update) the current user's LLM credentials, encrypted at rest."""
    if not body.api_base_url.startswith("http"):
        raise HTTPException(status_code=422, detail="api_base_url must be a valid URL")
    if not body.api_key:
        raise HTTPException(status_code=422, detail="api_key is required")

    api_key = body.api_key
    if "*" in api_key:
        existing = job_db.get_llm_config(user.user_id)
        if existing:
            api_key = existing["api_key"]
        else:
            raise HTTPException(status_code=422, detail="Invalid API key format")

    job_db.save_llm_config(
        owner_id=user.user_id,
        api_base_url=body.api_base_url,
        api_key=api_key,
        model_manager=body.model_manager or "gpt-4o-mini",
        model_worker=body.model_worker or "gpt-4o-mini",
        model_reviewer=body.model_reviewer or "gpt-4o-mini",
    )
    return {"saved": True}


@app.delete("/api/llm/config", status_code=200)
async def delete_llm_config(user: CurrentUser = Depends(get_current_user)):
    """Remove the current user's stored LLM credentials."""
    deleted = job_db.delete_llm_config(user.user_id)
    return {"deleted": deleted}


class LLMTestConnectionRequest(BaseModel):
    api_base_url: str
    api_key: str
    model_manager: Optional[str] = None
    model_worker: Optional[str] = None
    model_reviewer: Optional[str] = None


@app.post("/api/llm/test-connection")
async def test_llm_connection(
    body: LLMTestConnectionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Test LLM connection with the supplied credentials for each configured model."""
    api_key = body.api_key
    if "*" in api_key:
        existing = job_db.get_llm_config(user.user_id)
        if existing:
            api_key = existing["api_key"]
        else:
            return {"ok": False, "error": "Invalid API key"}

    models_to_test = {
        "Manager": body.model_manager or "gpt-4o-mini",
        "Worker": body.model_worker or "gpt-4o-mini",
        "Reviewer": body.model_reviewer or "gpt-4o-mini",
    }

    results = {}
    from src.llamaindex_crew.utils.llm_config import GenericLlamaLLM

    # Deduplicate models to avoid redundant network calls
    tested_models = {}
    for role, model_name in models_to_test.items():
        if model_name in tested_models:
            results[role] = tested_models[model_name]
            continue

        try:
            llm = GenericLlamaLLM(
                model=model_name,
                api_key=api_key,
                api_base=body.api_base_url,
                max_tokens=5,
            )
            resp = llm.complete("ping")
            if resp:
                tested_models[model_name] = {"ok": True}
            else:
                tested_models[model_name] = {"ok": False, "error": "Empty response"}
        except Exception as e:
            tested_models[model_name] = {"ok": False, "error": str(e)}

        results[role] = tested_models[model_name]

    # Verify if all tests passed
    failed = []
    for role, res in results.items():
        if not res["ok"]:
            failed.append(f"{role} Model '{models_to_test[role]}': {res.get('error')}")

    if not failed:
        return {"ok": True}
    return {
        "ok": False,
        "error": f"Connection tests failed: {'; '.join(failed)}"
    }


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
