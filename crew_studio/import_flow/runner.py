"""
Import analysis runner: git init, tech stack detection, tech_stack.md, optional LLM summary, file indexing.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".pytest_cache", "htmlcov",
    ".tox", "venv", ".venv", "dist", "build", "target", ".idea",
}
_SOURCE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
    ".rb", ".c", ".cpp", ".h", ".hpp", ".cs", ".swift",
    ".html", ".htm", ".css", ".scss", ".vue", ".sql", ".sh",
    ".yaml", ".yml", ".json", ".toml", ".xml", ".md",
}
# Artifacts written by this importer — exclude from "source" discovery so we index user code only.
_GENERATED_IMPORT_ARTIFACTS = frozenset({
    "tech_stack.md",
    "import_index_manifest.json",
})
# Common extensionless or doc-only files (e.g. GitHub's octocat/Hello-World `README`).
_KNOWN_DOC_FILENAMES = frozenset({
    "readme", "readme.txt", "license", "license.txt", "copying",
    "contributing", "changelog", "authors", "code_of_conduct",
    "makefile", "gnumakefile", "dockerfile", "containerfile",
    "gemfile", "rakefile", "procfile",
})
_CONFIG_FILES = (
    "pom.xml", "build.gradle", "build.gradle.kts", "package.json",
    "requirements.txt", "pyproject.toml", "Pipfile", "go.mod",
    "Cargo.toml", "composer.json", "Gemfile",
)
_MAX_INDEX_FILES = 180
_MAX_INDEX_TOTAL_CHARS = 400_000
_MAX_SINGLE_FILE_CHARS = 24_000


def _discover_source_files(workspace_path: Path) -> List[str]:
    results: List[str] = []
    for item in sorted(workspace_path.rglob("*")):
        if any(part in _SKIP_DIRS for part in item.parts):
            continue
        if not item.is_file():
            continue
        if item.name.lower() in _GENERATED_IMPORT_ARTIFACTS:
            continue
        try:
            rel = str(item.relative_to(workspace_path)).replace("\\", "/")
        except ValueError:
            continue
        base_l = item.name.lower()
        suf = item.suffix.lower()
        is_known_doc = base_l in _KNOWN_DOC_FILENAMES or (
            base_l.startswith("readme.") and base_l != "readme"
        )
        if suf in _SOURCE_EXTS or is_known_doc:
            results.append(rel)
    return sorted(results)


def _has_obvious_readme_or_docs(workspace_path: Path) -> bool:
    """True if we see README/LICENSE-style files (including extensionless ``README``)."""
    for item in workspace_path.rglob("*"):
        if any(part in _SKIP_DIRS for part in item.parts):
            continue
        if not item.is_file():
            continue
        if item.name.lower() in _GENERATED_IMPORT_ARTIFACTS:
            continue
        base_l = item.name.lower()
        if base_l in _KNOWN_DOC_FILENAMES or (
            base_l.startswith("readme.") and "readme" in base_l
        ):
            return True
    return False


def _read_snippet(path: Path, max_chars: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars]
    except OSError:
        return ""


def _detect_java_build(pom: Optional[Path], gradle: Optional[Path]) -> List[str]:
    hints: List[str] = []
    if pom and pom.is_file():
        hints.append("Maven (pom.xml)")
        xml = _read_snippet(pom, 8000).lower()
        if "spring-boot" in xml or "springframework" in xml:
            hints.append("Spring / Spring Boot (detected in pom.xml)")
        if "quarkus" in xml:
            hints.append("Quarkus (detected in pom.xml)")
        if "jakarta." in xml or "<jakarta." in xml:
            hints.append("Jakarta EE namespace in use")
    if gradle and gradle.is_file():
        hints.append("Gradle")
        g = _read_snippet(gradle, 8000).lower()
        if "spring-boot" in g:
            hints.append("Spring Boot (detected in Gradle)")
        if "quarkus" in g:
            hints.append("Quarkus (detected in Gradle)")
    return hints


def _detect_node_package(pkg: Optional[Path]) -> List[str]:
    if not pkg or not pkg.is_file():
        return []
    hints: List[str] = ["Node.js / npm or yarn (package.json)"]
    try:
        import json
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return hints
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    for name, label in (
        ("react", "React"),
        ("vue", "Vue"),
        ("@angular/core", "Angular"),
        ("next", "Next.js"),
        ("vite", "Vite"),
        ("express", "Express"),
    ):
        if name in deps:
            hints.append(label)
    return hints


def _detect_python(req: Optional[Path], pyproject: Optional[Path]) -> List[str]:
    hints: List[str] = []
    if req and req.is_file():
        hints.append("Python (requirements.txt)")
        t = _read_snippet(req, 4000).lower()
        for token, label in (
            ("django", "Django"),
            ("flask", "Flask"),
            ("fastapi", "FastAPI"),
            ("pytest", "pytest"),
        ):
            if token in t:
                hints.append(label)
    if pyproject and pyproject.is_file():
        hints.append("Python (pyproject.toml)")
        t = _read_snippet(pyproject, 6000).lower()
        if "[tool.poetry]" in t:
            hints.append("Poetry")
        if "django" in t:
            hints.append("Django")
        if "flask" in t:
            hints.append("Flask")
        if "fastapi" in t:
            hints.append("FastAPI")
    return hints


def _collect_stack_hints(workspace_path: Path) -> Dict[str, Any]:
    found_files = {name: None for name in _CONFIG_FILES}
    for root, _, files in os.walk(workspace_path):
        parts = Path(root).relative_to(workspace_path).parts
        if any(p in _SKIP_DIRS for p in parts):
            continue
        for f in files:
            if f in found_files and found_files[f] is None:
                found_files[f] = Path(root) / f

    hints: Dict[str, Any] = {
        "markers": [],
        "languages": set(),
        "top_level_dirs": [],
    }
    # Top-level dirs (first level only)
    if workspace_path.is_dir():
        hints["top_level_dirs"] = sorted(
            d.name for d in workspace_path.iterdir() if d.is_dir() and d.name not in _SKIP_DIRS
        )
        hints["top_level_files"] = sorted(
            f.name for f in workspace_path.iterdir()
            if f.is_file() and f.name.lower() not in _GENERATED_IMPORT_ARTIFACTS
        )

    pom = found_files.get("pom.xml")
    gradle = found_files.get("build.gradle") or found_files.get("build.gradle.kts")
    java_hints = _detect_java_build(pom, gradle)
    if java_hints:
        hints["markers"].extend(java_hints)
        hints["languages"].add("Java")

    pkg = found_files.get("package.json")
    node_hints = _detect_node_package(pkg)
    if node_hints:
        hints["markers"].extend(node_hints)
        hints["languages"].add("JavaScript/TypeScript")

    req = found_files.get("requirements.txt")
    pyproj = found_files.get("pyproject.toml")
    py_hints = _detect_python(req, pyproj)
    if py_hints:
        hints["markers"].extend(py_hints)
        hints["languages"].add("Python")

    if found_files.get("go.mod") and found_files["go.mod"].is_file():
        hints["markers"].append("Go (go.mod)")
        hints["languages"].add("Go")

    if found_files.get("Cargo.toml") and found_files["Cargo.toml"].is_file():
        hints["markers"].append("Rust (Cargo.toml)")
        hints["languages"].add("Rust")

    if not hints["markers"]:
        rels = _discover_source_files(workspace_path)
        for r in rels[:50]:
            ext = Path(r).suffix.lower()
            if ext in (".py",):
                hints["languages"].add("Python")
            elif ext in (".js", ".jsx", ".ts", ".tsx"):
                hints["languages"].add("JavaScript/TypeScript")
            elif ext == ".java":
                hints["languages"].add("Java")
            elif ext == ".go":
                hints["languages"].add("Go")

    if _has_obvious_readme_or_docs(workspace_path) and not hints["markers"]:
        hints["markers"].append(
            "Documentation-style files (README, LICENSE, …) only; "
            "no package or build manifests (e.g. package.json, pom.xml). "
            "Typical of minimal or sample repositories (including GitHub's Hello-World)."
        )
    if _has_obvious_readme_or_docs(workspace_path) and not hints["languages"]:
        hints["languages"].add("Plain text / documentation (no application source detected)")

    hints["languages"] = sorted(hints["languages"])
    return hints


def _build_tech_stack_markdown(
    workspace_path: Path,
    vision: str,
    hints: Dict[str, Any],
    listing_sample: str,
    llm_section: str,
) -> str:
    lines = [
        "# Imported project — detected tech stack",
        "",
        "## Job vision / description",
        vision or "(none provided)",
        "",
        "## Detected languages",
        ", ".join(hints.get("languages", [])) or "Unknown — inspect repository manually.",
        "",
        "## Markers & frameworks",
    ]
    for m in hints.get("markers", []):
        lines.append(f"- {m}")
    if not hints.get("markers"):
        lines.append("- (no standard markers found)")
    lines.extend([
        "",
        "## Top-level layout",
        "",
    ])
    tld = hints.get("top_level_dirs", [])
    tlf = hints.get("top_level_files") or []
    lines.append(f"- **Subdirectories:** {', '.join(tld) if tld else '(none — flat project root)'}")
    lines.append(f"- **Files in root:** {', '.join(tlf) if tlf else '(none)'}")
    lines.extend([
        "",
        "## File listing sample",
        "```",
        listing_sample[:12_000],
        "```",
        "",
    ])
    if llm_section:
        lines.extend([
            "## Project overview (LLM)",
            llm_section.strip(),
            "",
        ])
    lines.extend([
        "## Iteration notes",
        "- Use **Refine** in the Files UI to apply natural-language edits.",
        "- Prefer targeted file scope when possible for speed and accuracy.",
        "",
    ])
    return "\n".join(lines)


def _maybe_llm_overview(heuristic_summary: str, vision: str) -> str:
    try:
        from src.llamaindex_crew.utils.llm_config import get_llm_for_agent
        llm = get_llm_for_agent("worker", budget_callback=None)
        prompt = (
            "You are documenting an existing codebase for developers. Given the heuristic "
            "detection summary and optional user description, write 2–5 short bullet points "
            "on what the project likely is, main technologies, and how to run tests or build "
            "if inferable. If unknown, say what is unknown. Output markdown bullets only.\n\n"
            f"User description:\n{vision or '(none)'}\n\n"
            f"Heuristic summary:\n{heuristic_summary}\n"
        )
        resp = llm.complete(prompt)
        text = getattr(resp, "text", None) or str(resp)
        return text.strip()[:8000]
    except Exception as e:
        logger.warning("LLM tech_stack overview skipped: %s", e)
        return ""


def run_import_analysis(
    job_id: str,
    workspace_path: Path,
    job_db: Any,
    progress_callback: Callable[[str, int, Optional[str]], None],
    vision: str = "",
) -> None:
    """
    Analyze imported workspace: git, tech_stack.md, index key source files, mark job completed.
    """
    workspace_path = Path(workspace_path)
    progress_callback("import_analyzing", 5, "Initializing repository...")

    if os.getenv("ENABLE_GIT", "true").lower() in ("true", "1", "yes"):
        try:
            import git
            if not (workspace_path / ".git").exists():
                git.Repo.init(workspace_path)
                gi = workspace_path / ".gitignore"
                if not gi.exists():
                    gi.write_text(
                        "__pycache__/\n*.pyc\n.pytest_cache/\n.coverage\nhtmlcov/\n"
                        ".env\nnode_modules/\ndist/\nbuild/\n",
                        encoding="utf-8",
                    )
        except Exception as e:
            logger.warning("git init during import: %s", e)

    progress_callback("import_analyzing", 20, "Detecting tech stack...")
    hints = _collect_stack_hints(workspace_path)
    heuristic_md = "\n".join(
        [f"- {m}" for m in hints.get("markers", [])]
        + [f"- language: {l}" for l in hints.get("languages", [])]
    )

    from src.llamaindex_crew.tools.file_tools import file_lister  # noqa: WPS433
    listing = file_lister(".", workspace_path=str(workspace_path))

    progress_callback("import_analyzing", 40, "Generating tech_stack.md (LLM overview may run)...")
    llm_section = _maybe_llm_overview(heuristic_md, vision)
    md = _build_tech_stack_markdown(workspace_path, vision, hints, listing, llm_section)
    (workspace_path / "tech_stack.md").write_text(md, encoding="utf-8")

    progress_callback("import_analyzing", 55, "Indexing source files...")
    rels = _discover_source_files(workspace_path)
    to_index: List[str] = []
    total_chars = 0
    for rp in rels:
        if len(to_index) >= _MAX_INDEX_FILES:
            break
        fp = workspace_path / rp
        try:
            sz = fp.stat().st_size
        except OSError:
            continue
        if sz > _MAX_SINGLE_FILE_CHARS * 4:
            continue
        to_index.append(rp)
        total_chars += min(sz, _MAX_SINGLE_FILE_CHARS)
        if total_chars >= _MAX_INDEX_TOTAL_CHARS:
            break

    try:
        from src.llamaindex_crew.utils.document_indexer import DocumentIndexer
        indexer = DocumentIndexer(workspace_path, job_id)
        if to_index:
            indexer.index_artifacts(to_index)
        (workspace_path / "import_index_manifest.json").write_text(
            __import__("json").dumps({"indexed_files": to_index, "count": len(to_index)}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("DocumentIndexer during import: %s", e)

    progress_callback("import_analyzing", 100, "Import analysis complete.")

    import json as _runner_json
    existing_meta = {}
    try:
        raw = job_db.get_job(job_id).get("metadata")
        if isinstance(raw, dict):
            existing_meta = raw
        elif isinstance(raw, str):
            existing_meta = _runner_json.loads(raw)
    except Exception:
        pass
    existing_meta["import_analysis"] = True
    existing_meta["indexed_file_count"] = len(to_index)
    existing_meta["detected_languages"] = hints.get("languages", [])

    job_db.update_job(job_id, {
        "status": "queued",
        "current_phase": "awaiting_refinement",
        "progress": 100,
        "results": _runner_json.dumps({
            "import_analysis": True,
            "indexed_file_count": len(to_index),
            "detected_languages": hints.get("languages", []),
        }),
        "metadata": _runner_json.dumps(existing_meta),
    })
    logger.info("Import analysis completed for job %s — awaiting refinement", job_id)
