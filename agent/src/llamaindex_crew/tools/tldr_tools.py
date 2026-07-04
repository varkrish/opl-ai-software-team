"""
TldrTools — LlamaIndex FunctionTool wrappers for the `tldr` code-search CLI.

Provides graph-based and regex-based code search so refine/refactor agents can
understand codebase structure and call-sites before making changes.

Factory: create_tldr_tools(workspace_path, lang=None)
Helper:  detect_tldr_lang(workspace_path) -> Optional[str]
         append_tldr_tools(tools, workspace_path, lang=None) -> list
Graph:   read_call_graph(workspace_path) -> list[dict]
         format_call_graph_delta(edges, story_key) -> str

Workflow helpers (simple mode):
  run_tldr(args) -> str               public alias of the subprocess runner
  _workspace_has_indexable_source(ws) private helper
  _extract_search_terms(fp, task)     private helper
  prefetch_tldr_context(...)          pre-compute tldr context for prompt injection
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from functools import partial
from pathlib import Path
from typing import Any, List, Optional

from llama_index.core.tools import FunctionTool

logger = logging.getLogger(__name__)

# Output cap to keep prompts within LLM context budgets.
_MAX_OUTPUT_CHARS = 8000
_TLDR_TIMEOUT = 30  # seconds

# tldr --lang accepted values
_TLDR_VALID_LANGS = {
    "python", "typescript", "javascript", "go", "rust", "java",
    "c", "cpp", "ruby", "php", "kotlin", "swift", "csharp",
    "scala", "lua", "luau", "elixir",
}

# Keyword mapping for auto-detection from tech_stack.md content
_LANG_KEYWORDS: dict[str, set[str]] = {
    "typescript": {"typescript", "next.js", "nestjs", "tsx", "nuxt", "angular"},
    "javascript": {"javascript", "node", "express", "react", "vue", "npm", "vite"},
    "python":     {"python", "flask", "fastapi", "django", "pip", "pyproject.toml", "uvicorn"},
    "java":       {"java", "spring boot", "spring", "quarkus", "maven", "gradle", "pom.xml"},
    "kotlin":     {"kotlin", "ktor"},
    "go":         {"golang", " go ", "gin ", "echo framework", "fiber"},
    "rust":       {"rust", "cargo", "actix", "axum"},
    "ruby":       {"ruby", "rails", "sinatra"},
    "csharp":     {"c#", "csharp", ".net", "aspnet", "blazor"},
    "swift":      {"swift", "swiftui", "vapor"},
    "php":        {"php", "laravel", "symfony"},
    "cpp":        {"c++", "cpp", "cmake"},
    "scala":      {"scala", "play framework", "akka"},
    "elixir":     {"elixir", "phoenix"},
}

# File extension fallback for when no tech_stack.md is present
_EXT_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".cs": "csharp",
    ".swift": "swift",
    ".php": "php",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".lua": "lua",
}

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", "venv", ".venv"}


def detect_tldr_lang(workspace_path: Path) -> Optional[str]:
    """Detect the primary language of a workspace for use as tldr --lang.

    Reads tech_stack.md first; falls back to file-extension frequency count.
    Returns one of tldr's valid --lang values or None if undetectable.
    """
    workspace_path = Path(workspace_path)
    tech_stack_file = workspace_path / "tech_stack.md"

    if tech_stack_file.exists():
        try:
            content = tech_stack_file.read_text(encoding="utf-8", errors="replace").lower()
            # typescript must come before javascript (it's a superset — check more specific first)
            for lang in ["typescript", "kotlin", "csharp", "python", "java", "javascript",
                         "go", "rust", "ruby", "swift", "php", "cpp", "scala", "elixir"]:
                if any(kw in content for kw in _LANG_KEYWORDS.get(lang, set())):
                    logger.debug("detect_tldr_lang: detected %r from tech_stack.md", lang)
                    return lang
        except Exception as e:
            logger.warning("detect_tldr_lang: could not read tech_stack.md: %s", e)

    # Fallback: count source file extensions
    ext_counts: dict[str, int] = {}
    try:
        for p in workspace_path.rglob("*"):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.is_file():
                lang = _EXT_LANG_MAP.get(p.suffix.lower())
                if lang:
                    ext_counts[lang] = ext_counts.get(lang, 0) + 1
    except Exception as e:
        logger.warning("detect_tldr_lang: extension scan failed: %s", e)

    if ext_counts:
        detected = max(ext_counts, key=lambda k: ext_counts[k])
        logger.debug("detect_tldr_lang: detected %r from file extensions (%s)", detected, ext_counts)
        return detected

    return None


def run_tldr(args: list[str]) -> str:
    """Run tldr with the given argument list and return truncated stdout or an error string."""
    tldr_bin = shutil.which("tldr")
    if not tldr_bin:
        return "tldr is not installed or not in PATH. Install with: pip install llm-tldr"

    try:
        result = subprocess.run(
            [tldr_bin] + args,
            capture_output=True,
            text=True,
            timeout=_TLDR_TIMEOUT,
        )
        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            err = result.stderr.strip()[:500]
            return f"tldr error (exit {result.returncode}): {err}"
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[:_MAX_OUTPUT_CHARS] + f"\n... (truncated to {_MAX_OUTPUT_CHARS} chars)"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"tldr timed out after {_TLDR_TIMEOUT}s"
    except FileNotFoundError:
        return "tldr is not installed or not in PATH. Install with: pip install llm-tldr"
    except Exception as e:
        return f"tldr failed: {e}"


# Backward-compatibility alias — existing callers that reference _run_tldr keep working.
_run_tldr = run_tldr


# ── Source extensions considered "indexable" by tldr ─────────────────────────
_INDEXABLE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".kt", ".kts",
    ".go", ".rs", ".rb", ".cs", ".swift",
    ".php", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
    ".scala", ".ex", ".exs", ".lua",
})

# Directory names that contain test code (not indexable source)
_TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs"})

# Stop-words excluded from search term extraction
_SEARCH_STOP_WORDS = frozenset({
    "a", "an", "the", "in", "of", "and", "to", "with", "is", "it",
    "for", "on", "at", "by", "be", "as", "or", "do", "if", "not",
    "use", "add", "get", "set", "new", "all", "any", "each", "make",
    "that", "this", "from", "when", "then", "than", "also", "into",
    "implement", "create", "build", "write", "update", "generate",
    "function", "method", "class", "module", "file", "type",
    "using", "with", "should", "must", "will", "has", "have",
})


def _workspace_has_indexable_source(workspace: Path) -> bool:
    """Return True when workspace contains at least one non-test source file.

    Used to gate tldr calls: no point running structure/search on an empty
    workspace that hasn't generated any files yet.

    Returns False for:
    - Empty workspaces
    - Workspaces that contain only test files (files in tests/ directories or
      named test_*.py / *_test.py)
    """
    workspace = Path(workspace)
    try:
        for p in workspace.rglob("*"):
            if not p.is_file():
                continue
            # Skip hidden/cache dirs
            try:
                rel_parts = p.relative_to(workspace).parts
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel_parts):
                continue
            # Skip files inside test directories
            if any(part in _TEST_DIR_NAMES for part in rel_parts[:-1]):
                continue
            # Skip test files by name
            name = p.name
            if name.startswith("test_") or name.endswith("_test.py"):
                continue
            # Accept any known source extension
            if p.suffix.lower() in _INDEXABLE_EXTENSIONS:
                return True
    except Exception as exc:
        logger.debug("_workspace_has_indexable_source: scan failed: %s", exc)
    return False


def _extract_search_terms(file_path: str, task: Any) -> list[str]:
    """Extract 1–3 search terms from the target file path stem + task description.

    The path stem is always the primary term.  Additional terms come from the
    task description after filtering stop-words and single-character tokens.
    """
    terms: list[str] = []

    # Primary: path stem (e.g. "calculator" from "src/calculator/calculator.py")
    stem = Path(file_path).stem
    if len(stem) > 1 and stem.lower() not in _SEARCH_STOP_WORDS:
        terms.append(stem)

    # Secondary: meaningful words from task description
    description = (getattr(task, "description", "") or "").lower()
    for word in description.split():
        word = word.strip(".,;:!?\"'()[]{}|")
        if len(word) <= 1:
            continue
        if word in _SEARCH_STOP_WORDS:
            continue
        if word in {t.lower() for t in terms}:
            continue
        terms.append(word)
        if len(terms) >= 3:
            break

    # Fallback: return the stem only when it has a meaningful length
    if not terms and stem and len(stem) > 1:
        terms.append(stem)

    return terms[:3]


def _is_useful_tldr_output(output: str) -> bool:
    """Return True if the tldr output contains meaningful content (not an error string)."""
    if not output or not output.strip():
        return False
    lower = output.strip().lower()
    if lower.startswith("tldr"):
        return False
    if lower == "(no output)":
        return False
    return True


def prefetch_tldr_context(
    workspace_path: Path,
    file_path: str,
    task: Any,
    completed_files: int,
    lang: Optional[str],
    structure_cache: dict,
    config: Any,
) -> str:
    """Pre-compute tldr context for simple-mode file generation.

    Mirrors the ``prefetch_skills()`` pattern: runs tldr subprocesses in the
    workflow and returns a combined string for injection into ``build_file_prompt``.
    No change to ReAct agents or their tool loops.

    Gating (returns "" immediately):
    - ``config.simple_mode_tldr_enabled`` is False
    - tldr binary not in PATH
    - workspace has no indexable source AND completed_files < min_threshold

    Sections included (in order):
    - ``structure`` — once per workspace, cached in ``structure_cache``
    - ``search`` — once per term from ``_extract_search_terms``
    - ``impact`` — only when the target file already exists on disk (brownfield)

    The combined result is truncated to ``config.simple_mode_tldr_max_chars``
    (default 6000 chars).
    """
    workspace_path = Path(workspace_path)

    # Gate 1: config switch
    enabled = getattr(config, "simple_mode_tldr_enabled", True) if config is not None else True
    if not enabled:
        return ""

    # Gate 2: tldr must be available
    if not shutil.which("tldr"):
        logger.debug("prefetch_tldr_context: tldr not in PATH — skipping")
        return ""

    # Gate 3: workspace must have source files OR enough completed files
    min_files = getattr(config, "simple_mode_tldr_min_completed_files", 1) if config else 1
    has_source = _workspace_has_indexable_source(workspace_path)
    if not has_source and completed_files < min_files:
        logger.debug(
            "prefetch_tldr_context: no indexable source (completed_files=%d < min=%d) — skipping",
            completed_files, min_files,
        )
        return ""

    max_chars: int = getattr(config, "simple_mode_tldr_max_chars", 6000) if config else 6000
    include_structure: bool = getattr(config, "simple_mode_tldr_include_structure", True) if config else True
    ws_str = str(workspace_path)

    sections: list[str] = []

    try:
        # ── Structure (once per workspace, cached) ────────────────────────────
        if include_structure:
            cache_key = ws_str
            if cache_key not in structure_cache:
                args: list[str] = ["structure", ws_str]
                if lang:
                    args += ["--lang", lang]
                structure_cache[cache_key] = run_tldr(args)
            struct_out = structure_cache[ws_str]
            if _is_useful_tldr_output(struct_out):
                sections.append(struct_out)

        # ── Search (per extracted term) ───────────────────────────────────────
        terms = _extract_search_terms(file_path, task)
        for term in terms:
            search_out = run_tldr(["search", term, ws_str])
            if _is_useful_tldr_output(search_out):
                sections.append(search_out)

        # ── Impact (only when target file already exists on disk) ─────────────
        target_path = workspace_path / file_path
        if target_path.exists() and terms:
            symbol = terms[0]
            impact_args: list[str] = ["impact", symbol, ws_str]
            if lang:
                impact_args += ["--lang", lang]
            impact_out = run_tldr(impact_args)
            if _is_useful_tldr_output(impact_out):
                sections.append(impact_out)

    except Exception as exc:
        logger.warning("prefetch_tldr_context: unexpected error: %s", exc)
        return ""

    result = "\n\n".join(sections)
    if len(result) > max_chars:
        result = result[:max_chars]

    return result


def _code_search(pattern: str, context_lines: int = 3, *, workspace: str) -> str:
    """Search the codebase for a regex pattern. Returns matching lines with surrounding context.

    Use BEFORE editing to find all usages of a function, class, or variable you plan to change.
    """
    args = ["search", pattern, workspace, "-C", str(context_lines)]
    return _run_tldr(args)


def _code_structure(*, workspace: str, lang: Optional[str]) -> str:
    """Show classes, functions, and exports across the project.

    Use at the start of a refactor or cross-cutting change to understand project layout
    without reading every file individually.
    """
    args = ["structure", workspace]
    if lang:
        args += ["--lang", lang]
    return _run_tldr(args)


def _code_context(entry: str, depth: int = 2, *, workspace: str, lang: Optional[str]) -> str:
    """Get the call-chain context for a function or Class.method.

    entry: function name or Class.method (e.g. "run_refinement" or "RefinementAgent.run")
    depth: how many call levels to show (default 2)

    Use to understand what a function calls and who it interacts with before editing it.
    """
    args = ["context", entry, "--project", workspace, "--depth", str(depth)]
    if lang:
        args += ["--lang", lang]
    return _run_tldr(args)


# Workspace artifacts produced by the crew pipeline — not "existing product code".
_SOLUTIONING_SKIP_NAMES = frozenset({
    "agent_backstories.json",
    "agent_prompts.json",
    "apps.json",
    "app.json",
    "crew_errors.log",
    "delivery_mode_triage.json",
    "rag_index_manifest.json",
    "skill_prefetch.json",
    "smoke_test_container.log",
    "solution_candidates.json",
    "solution_spec.md",
    "test_plan.md",
    "validation_report.json",
})
_SOLUTIONING_SKIP_PREFIXES = (
    "solution_critique_pass_",
    "index_",
    "state_",
    "tasks_",
)


def _is_solutioning_artifact(rel_path: str) -> bool:
    """Return True when *rel_path* is a crew-generated artifact, not product code."""
    name = Path(rel_path).name
    if name in _SOLUTIONING_SKIP_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in _SOLUTIONING_SKIP_PREFIXES)


def list_workspace_source_files(workspace_path: Path, *, max_files: int = 60) -> list[str]:
    """List relative source/config paths in *workspace_path* for brownfield context."""
    workspace_path = Path(workspace_path)
    if not workspace_path.is_dir():
        return []

    paths: list[str] = []
    try:
        for p in sorted(workspace_path.rglob("*")):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(workspace_path).as_posix()
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in Path(rel).parts):
                continue
            if _is_solutioning_artifact(rel):
                continue
            if p.suffix.lower() in _INDEXABLE_EXTENSIONS or p.name in {
                "package.json", "pyproject.toml", "requirements.txt", "pom.xml",
                "build.gradle", "Dockerfile", "Containerfile", "README.md",
            }:
                paths.append(rel)
            if len(paths) >= max_files:
                break
    except Exception as exc:
        logger.debug("list_workspace_source_files: scan failed: %s", exc)
    return paths


def build_solutioning_codebase_context(
    workspace_path: Path,
    *,
    max_chars: int = 6000,
    max_files: int = 60,
) -> str:
    """Build brownfield context for solutioning agents (file list + optional tldr structure).

    Returns an empty string when the workspace has no meaningful existing code.
    """
    workspace_path = Path(workspace_path)
    source_files = list_workspace_source_files(workspace_path, max_files=max_files)
    if not source_files and not _workspace_has_indexable_source(workspace_path):
        return ""

    parts: list[str] = ["EXISTING CODEBASE (brownfield — account for this in your solution):"]
    if source_files:
        parts.append("Existing project files:")
        parts.append(", ".join(source_files))

    if _workspace_has_indexable_source(workspace_path) and shutil.which("tldr"):
        lang = detect_tldr_lang(workspace_path)
        args: list[str] = ["structure", str(workspace_path)]
        if lang:
            args += ["--lang", lang]
        struct_out = run_tldr(args)
        if _is_useful_tldr_output(struct_out):
            parts.append("Codebase structure (tldr):")
            parts.append(struct_out)

    result = "\n".join(parts).strip()
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n... (truncated to {max_chars} chars)"
    return result


_TLDR_AGENT_BACKSTORY = (
    "\n\nYou have code_search, code_structure, code_context, and code_impact tools. "
    "When the workspace contains existing code, use code_structure (and code_search as needed) "
    "to understand what is already built before proposing architectural changes."
)


def _code_impact(func: str, depth: int = 3, *, workspace: str, lang: Optional[str]) -> str:
    """Find all callers of a function (reverse call graph).

    func: the function name to trace callers of (e.g. "run_refinement")
    depth: how many caller levels to follow (default 3)

    Use BEFORE renaming, deleting, or changing the signature of a function to ensure
    every call site is updated.
    """
    args = ["impact", func, workspace, "--depth", str(depth)]
    if lang:
        args += ["--lang", lang]
    return _run_tldr(args)


_CALL_GRAPH_CANDIDATES = [
    ".tldr/cache/call_graph.json",
    ".tldr/cache/call_graph-backend.json",
    ".tldr/cache/call_graph-react.json",
]


def read_call_graph(workspace_path: Path) -> list[dict]:
    """Read and merge edges from all tldr call graph cache files in the workspace.

    Only searches workspace/.tldr/cache/ — never parent directories, to avoid
    accidentally reading a host-repo call graph into a generated project's index.
    Deduplicates by (from_file, from_func, to_file, to_func) key.
    Returns an empty list if no cache files are found or all are malformed.
    """
    workspace_path = Path(workspace_path)
    edges: list[dict] = []
    seen: set[tuple] = set()

    for candidate in _CALL_GRAPH_CANDIDATES:
        p = workspace_path / candidate
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for e in data.get("edges", []):
                key = (
                    e.get("from_file", ""),
                    e.get("from_func", ""),
                    e.get("to_file", ""),
                    e.get("to_func", ""),
                )
                if key not in seen:
                    seen.add(key)
                    edges.append(e)
        except Exception as exc:
            logger.warning("read_call_graph: could not read %s: %s", p, exc)

    return edges


def refresh_call_graph(workspace_path: Path, lang: Optional[str] = None) -> None:
    """Run `tldr structure` to force tldr to rebuild its call graph cache.

    This is a side-effect call — the return value (text output) is discarded.
    The real goal is to ensure .tldr/cache/call_graph.json is current before
    index_story_memory reads it.  Silently no-ops if tldr is not installed.
    """
    workspace_path = Path(workspace_path)
    args = ["structure", str(workspace_path)]
    if lang:
        args += ["--lang", lang]
    output = _run_tldr(args)
    if output.startswith("tldr is not installed") or output.startswith("tldr failed"):
        logger.warning("refresh_call_graph: %s", output)
    else:
        logger.debug("refresh_call_graph: call graph updated for %s", workspace_path)


def format_call_graph_delta(new_edges: list[dict], story_key: str) -> str:
    """Format new call graph edges as indexed text for RAG injection.

    Caps at 200 edges to keep chunks within LLM context budgets.
    Returns empty string if new_edges is empty.
    """
    if not new_edges:
        return ""
    lines = [f"Story {story_key} introduced the following call relationships:"]
    for edge in new_edges[:200]:
        lines.append(
            f"  {edge.get('from_file', '?')}::{edge.get('from_func', '?')}"
            f" -> {edge.get('to_file', '?')}::{edge.get('to_func', '?')}"
        )
    return "\n".join(lines)


def create_tldr_tools(workspace_path: Path, lang: Optional[str] = None) -> List[FunctionTool]:
    """Create tldr code-search tools bound to a specific workspace path.

    Args:
        workspace_path: The job workspace root. All tools operate within this directory.
        lang: Optional tldr --lang value (e.g. "python", "java", "typescript").
              When None, tools that require --lang omit the flag (tldr auto-detects).
              Use detect_tldr_lang(workspace_path) to resolve this before calling.

    Returns:
        List of 4 FunctionTool instances: code_search, code_structure, code_context, code_impact.
    """
    ws = str(workspace_path)

    return [
        FunctionTool.from_defaults(
            fn=partial(_code_search, workspace=ws),
            name="code_search",
            description=(
                "Search the codebase for a regex pattern. Returns matching lines with context. "
                "Args: pattern (regex string), context_lines (int, default 3). "
                "Use BEFORE editing to locate all usages of a function, class, or symbol you plan to change."
            ),
        ),
        FunctionTool.from_defaults(
            fn=partial(_code_structure, workspace=ws, lang=lang),
            name="code_structure",
            description=(
                "Show classes, functions, and exports across the entire project. "
                "No arguments required. "
                "Use at task start to understand the project map before reading individual files."
            ),
        ),
        FunctionTool.from_defaults(
            fn=partial(_code_context, workspace=ws, lang=lang),
            name="code_context",
            description=(
                "Get the call-chain context for a function or method. "
                "Args: entry (str, e.g. 'run_refinement' or 'MyClass.my_method'), depth (int, default 2). "
                "Use to understand what a function calls and how it integrates before modifying it."
            ),
        ),
        FunctionTool.from_defaults(
            fn=partial(_code_impact, workspace=ws, lang=lang),
            name="code_impact",
            description=(
                "Find all callers of a function (reverse call graph). "
                "Args: func (str, function name), depth (int, default 3). "
                "Use BEFORE renaming, deleting, or changing a function signature to find every call site."
            ),
        ),
    ]


TLDR_TOOL_NAMES = frozenset({
    "code_search",
    "code_structure",
    "code_context",
    "code_impact",
})


def append_tldr_tools(
    tools: List[FunctionTool],
    workspace_path: Path,
    lang: Optional[str] = None,
) -> List[FunctionTool]:
    """Append tldr code-search tools for *workspace_path*, skipping duplicates."""
    wp = Path(workspace_path)
    detected = lang if lang is not None else detect_tldr_lang(wp)
    existing = {t.metadata.name for t in tools if getattr(t, "metadata", None)}
    added = 0
    for tool in create_tldr_tools(wp, lang=detected):
        name = tool.metadata.name
        if name in existing:
            continue
        tools.append(tool)
        existing.add(name)
        added += 1
    if added:
        logger.debug(
            "append_tldr_tools: added %d tool(s) for workspace=%s lang=%r",
            added, wp, detected,
        )
    return tools
