"""
Wiring Contract binding utils for LlamaIndex Crew.
Defines schema, validation, loading, writing, slice_for_file, and validation gate logic.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

WIRING_CONTRACT_FILENAME = "wiring_contract.json"

# Injected into solution architect / designer prompts — compact jq patches, not full JSON.
WIRING_PATCH_EMIT_INSTRUCTIONS = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REQUIRED — WIRING PATCH (jq program, token-efficient)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The pipeline seeds packages/files from your paths. You MUST also emit a short jq filter
(NOT shell) that sets module, language, package ownership, key signatures, and deps.

<wiring_patch>
.module = "my-app"
| .language = "go"
| .packages["internal/api"].owns = ["CreateHandler", "DeleteHandler"]
| .packages["internal/service"].owns = ["NewService", "Create"]
| .symbols["internal/service.Create"] = {"package":"internal/service","signature":"func (s *Service) Create(ctx context.Context, name string) (string, error)","exports":["Create"]}
| .symbols["internal/api.CreateHandler"] = {"package":"internal/api","signature":"func CreateHandler(svc *service.Service) http.HandlerFunc","exports":["CreateHandler"]}
| .deps += [{"from":"internal/api","to":"internal/service"}]
| .deps += [{"from":"cmd/my-app","to":"internal/api"}]
</wiring_patch>

Rules (language-neutral):
- jq filter syntax only (chains with |). Do NOT emit shell.
- .module = canonical local import/package root (never a useless root like "cmd" alone).
- .language = primary language (go|python|typescript|javascript|java|rust|…).
- One package per concern — no parallel packages for the same ownership (api vs handlers).
- HTTP/API handlers live in the API package; entrypoint/main only wires the mux — it does not reimplement handlers.
- Symbol keys MUST be "package.SymbolName" (qualified). Signatures use the project's language.
- .deps are within-tier package edges; HTTP boundaries stay in api_contract.yaml.
- Also list the same public APIs as interface contracts in the prose/design so they can be recovered if jq fails.
"""

# Legacy full-JSON emit (still parsed when present).
WIRING_CONTRACT_EMIT_INSTRUCTIONS = WIRING_PATCH_EMIT_INSTRUCTIONS + """
Legacy alternative (larger output): you may instead emit <wiring_contract>{...full JSON...}</wiring_contract>
with the same fields (module, language, packages, symbols with qualified keys, deps).
"""

# Short repair prompt when design locked without planned APIs.
WIRING_PATCH_REPAIR_PROMPT = """
Your previous design is missing a usable wiring contract (module + package owns and/or signatures).

Emit ONLY a <wiring_patch>...</wiring_patch> jq program (no prose) that sets:
- .module (real import root, not bare "cmd")
- .language
- .packages["<pkg>"].owns for each important package
- .symbols["<pkg>.<Name>"] with language-real signatures for public cross-package APIs
- .deps between packages (api → service/domain; entrypoint → api)

Do not invent parallel HTTP packages. Handlers belong in the API package; main only wires them.
"""

# Blacklist for registration: reject dangerous binaries and image assets only.
REJECTED_FILE_EXTENSIONS = frozenset({
    # Dangerous / executable / native binaries
    'exe', 'dll', 'so', 'dylib', 'o', 'a', 'bin', 'com',
    'bat', 'cmd', 'msi', 'scr',
    'class', 'pyc', 'pyo', 'wasm',
    # Pictures / media images
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'bmp',
    'tif', 'tiff', 'heic', 'avif',
})

# Extensionless basenames that are still real project artifacts.
EXTENSIONLESS_FILENAMES = frozenset({
    'dockerfile', 'containerfile', 'makefile', 'gnumakefile',
    'gemfile', 'procfile', 'rakefile', 'brewfile',
    'license', 'licence', 'notice', 'authors', 'contributors',
    'copying', 'changelog', 'changes', 'history',
    'cargo.lock',  # defensive; normally matched via .lock extension
    'pipfile', 'vagrantfile', 'jenkinsfile',
})

def _is_valid_file_path(name: str) -> bool:
    """Return True if *name* looks like a real file path, not numbered-list junk or a blacklisted type."""
    if not name or len(name) < 2:
        return False
    # Reject purely numeric prefixes like "1.", "2.", "23."
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    if stem.isdigit():
        return False
    basename = name.rsplit('/', 1)[-1]
    if '.' not in basename:
        return basename.lower() in EXTENSIONLESS_FILENAMES
    if basename.lower() in EXTENSIONLESS_FILENAMES:
        return True
    ext = basename.rsplit('.', 1)[1].lower()
    if not ext:
        return False
    if ext in REJECTED_FILE_EXTENSIONS:
        return False
    return True

def extract_files_with_descriptions_from_tech_stack(content: str) -> List[Dict[str, str]]:
    """Extract file paths with descriptions from a tree structure in markdown code blocks."""
    regions: List[str] = []
    current_lines: List[str] = []
    in_block = False

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if current_lines:
                regions.append("\n".join(current_lines))
                current_lines = []
            in_block = not in_block
            continue
        current_lines.append(line)
    if current_lines:
        regions.append("\n".join(current_lines))

    TREE_CHARS_RE = re.compile(r'[├└│─]')
    entries: List[Dict[str, str]] = []

    for block in regions:
        if not TREE_CHARS_RE.search(block):
            continue

        dir_stack: List[tuple] = []
        root_dir: Optional[str] = None

        for line in block.splitlines():
            tree_chars = re.match(r'^([\s│]*[├└─\s]*)', line)
            indent = len(tree_chars.group(1).replace('│', ' ').replace('├', ' ')
                         .replace('└', ' ').replace('─', ' ')) if tree_chars else 0

            entry_match = re.search(
                r'[├└│─\s]*([a-zA-Z0-9_.\-][a-zA-Z0-9_/.\-]*/?)(?:\s+#\s*(.*))?',
                line,
            )
            if not entry_match:
                continue

            name = entry_match.group(1).strip()
            description = (entry_match.group(2) or "").strip()

            while dir_stack and dir_stack[-1][0] >= indent:
                dir_stack.pop()

            if name.endswith('/'):
                dir_name = name.rstrip('/')
                if root_dir is None and not dir_stack:
                    root_dir = dir_name
                dir_stack.append((indent, dir_name))
            elif _is_valid_file_path(name):
                prefix = "/".join(d[1] for d in dir_stack)
                full_path = f"{prefix}/{name}" if prefix else name
                if root_dir and full_path.startswith(root_dir + "/"):
                    full_path = full_path[len(root_dir) + 1:]
                entries.append({"path": full_path, "description": description})

    _PLAIN_PATH_RE = re.compile(
        r'^([a-zA-Z0-9_.\-][a-zA-Z0-9_/.\-]*\.[a-zA-Z0-9]+)(?:\s+#\s*(.*))?\s*$'
    )
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            continue
        if stripped.startswith("#") and not _PLAIN_PATH_RE.match(stripped.lstrip("#").strip()):
            continue
        match = _PLAIN_PATH_RE.match(stripped)
        if match and _is_valid_file_path(match.group(1)):
            entries.append({
                "path": match.group(1).strip(),
                "description": (match.group(2) or "").strip(),
            })

    seen: set = set()
    deduped: List[Dict[str, str]] = []
    for e in entries:
        if e["path"] not in seen:
            seen.add(e["path"])
            deduped.append(e)
    return deduped


_FORBIDDEN_PATCH_SUBSTRINGS = (
    "input_filename",
    "@json:",
    "$env",
    "env(",
    "debug(",
    "import ",
    "include ",
    "def ",
    "test(",
    "try ",
    "catch ",
)


class WiringContractError(ValueError):
    """Raised when wiring contract validation fails."""
    pass


def stamp_contract_meta(contract: dict, *, source: str, enforcement: str | None = None) -> dict:
    """Attach non-binding metadata used for enforcement level selection."""
    out = dict(contract)
    meta = dict(out.get("_meta") or {})
    meta["source"] = source
    if enforcement:
        meta["enforcement"] = enforcement
    elif source == "emitted" and (out.get("symbols") or out.get("deps")):
        meta["enforcement"] = "strict"
    else:
        meta["enforcement"] = "relaxed"
    out["_meta"] = meta
    return out


def is_strict_wiring_enforcement(contract: dict | None) -> bool:
    """Whether path-level wiring gates may block progress (off by default for small models)."""
    if os.environ.get("WIRING_CONTRACT_STRICT", "").lower() in ("1", "true", "yes"):
        return True
    if not contract:
        return False
    meta = contract.get("_meta") or {}
    if meta.get("enforcement") == "strict":
        return True
    if meta.get("enforcement") == "relaxed":
        return False
    source = meta.get("source")
    if source in ("emitted", "jq-patch") and contract.get("symbols"):
        return True
    return False


def resolve_jq_bin() -> Optional[str]:
    """Return jq executable path if installed."""
    return shutil.which("jq")


def parse_emitted_wiring_patch(*texts: str) -> Optional[str]:
    """Parse the first <wiring_patch> jq program from texts (first text wins)."""
    for text in texts:
        if not text or not text.strip():
            continue
        tag_match = re.search(
            r"<wiring_patch>\s*(.*?)\s*</wiring_patch>",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if tag_match:
            patch = tag_match.group(1).strip()
            if patch:
                return patch
    return None


def _normalize_jq_patch_program(patch: str) -> str:
    """Strip fences and ensure the program is a jq filter starting with '.'"""
    program = patch.strip()
    program = re.sub(r"^```(?:jq)?\s*", "", program, flags=re.IGNORECASE)
    program = re.sub(r"\s*```$", "", program)
    program = program.strip()
    if not program:
        return program
    if not program.startswith("."):
        program = "." + program
    return program


def _patch_program_is_safe(patch: str) -> bool:
    lowered = patch.lower()
    return not any(token in lowered for token in _FORBIDDEN_PATCH_SUBSTRINGS)


def apply_wiring_patch(seed: dict, patch_program: str) -> Optional[dict]:
    """Apply an LLM-emitted jq filter to *seed*; return validated contract or None."""
    jq_bin = resolve_jq_bin()
    if not jq_bin:
        logger.warning("jq not installed; skipping wiring patch")
        return None
    if not seed or not patch_program or not patch_program.strip():
        return None

    program = _normalize_jq_patch_program(patch_program)
    if not program:
        return None
    if not _patch_program_is_safe(program):
        logger.warning("wiring patch rejected: contains forbidden jq constructs")
        return None

    try:
        result = subprocess.run(
            [jq_bin, "-c", program],
            input=json.dumps(seed),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("apply_wiring_patch subprocess failed: %s", exc)
        return None

    if result.returncode != 0:
        logger.warning("jq wiring patch failed: %s", (result.stderr or "").strip())
        return None

    try:
        parsed = json.loads((result.stdout or "").strip())
    except json.JSONDecodeError as exc:
        logger.warning("jq wiring patch produced invalid JSON: %s", exc)
        return None

    try:
        validated = validate_wiring_contract(parsed)
    except WiringContractError as exc:
        logger.warning("jq wiring patch result failed validation: %s", exc)
        return None

    enforcement = "strict" if (validated.get("symbols") or validated.get("deps")) else "relaxed"
    return stamp_contract_meta(validated, source="jq-patch", enforcement=enforcement)


# ── Planned-emit helpers (language-neutral) ─────────────────────────────────

_WEAK_MODULE_NAMES = frozenset({
    "cmd", "src", "app", "main", "pkg", "lib", "internal", "unknown",
    "project", "root", "code", "backend", "frontend",
})

# Signature-like lines across common languages (Go/Python/TS/JS/Java/Rust/C#…).
# Second arm requires a real modifier keyword (no bare "Name (" / prose "clients (").
_SIG_LINE_RE = re.compile(
    r"(?:"
    r"(?:pub(?:\(crate\))?\s+)?(?:async\s+)?(?:fn|func|def|function|sub)\s+"
    r"(?:\([^)]*\)\s*)?"  # optional Go receiver
    r"([A-Za-z_][\w]*)\s*\([^;{]*"  # name + params start
    r"|"
    r"(?:(?:public|protected|private|internal|static|async|export|override|virtual|final|abstract)\s+)+"
    r"(?:[A-Za-z_][\w.<>,\[\]?]+\s+)*"  # optional return type
    r"([A-Za-z_][\w]*)\s*\([^;{]*\)"  # Java/C#/TS method
    r")",
    re.IGNORECASE,
)

_PKG_HEADING_RE = re.compile(
    r"(?:"
    r"^#{1,4}\s+`?([a-zA-Z0-9_./-]+/[a-zA-Z0-9_./-]+)`?"  # ## internal/api
    r"|"
    r"^(?:package|module|path|directory|folder)\s*[:=]\s*`?([a-zA-Z0-9_./-]+)`?"
    r"|"
    r"^(?:\*\*)?(?:package|module)\s+`?([a-zA-Z0-9_./-]+/`?[a-zA-Z0-9_./-]*)`?"
    r")",
    re.MULTILINE | re.IGNORECASE,
)


def symbol_key(package: str, name: str) -> str:
    """Qualified symbol key: ``package.SymbolName`` (language-neutral)."""
    pkg = normalize_workspace_path(package or "")
    nm = (name or "").strip()
    if not nm:
        return ""
    if not pkg or pkg == ".":
        return nm
    if nm.startswith(pkg + "."):
        return nm
    return f"{pkg}.{nm}"


def _is_weak_module_name(name: str) -> bool:
    n = (name or "").strip().lower().rstrip("/")
    if not n:
        return True
    if "/" in n:
        # github.com/org/sandbox-api is fine; bare cmd/foo still ok if leaf isn't weak-only
        leaf = n.split("/")[-1]
        return leaf in _WEAK_MODULE_NAMES and n.count("/") < 2
    return n in _WEAK_MODULE_NAMES


def contract_has_planned_apis(contract: dict | None) -> bool:
    """True when contract has usable planned owns and/or non-empty symbol signatures."""
    if not contract:
        return False
    packages = contract.get("packages") or {}
    for pkg_data in packages.values():
        if isinstance(pkg_data, dict) and (pkg_data.get("owns") or []):
            return True
    symbols = contract.get("symbols") or {}
    for sym_data in symbols.values():
        if not isinstance(sym_data, dict):
            continue
        sig = (sym_data.get("signature") or "").strip()
        if sig and not _is_placeholder_signature(sig):
            return True
        if sig:
            return True  # name-only still counts as planned ownership signal
    return False


def normalize_symbol_keys(contract: dict) -> dict:
    """Rewrite bare symbol keys to ``package.name`` using each symbol's package field."""
    if not contract:
        return contract
    out = json.loads(json.dumps(contract))
    symbols = out.get("symbols") or {}
    if not isinstance(symbols, dict):
        return out
    normalized: Dict[str, Any] = {}
    for key, data in symbols.items():
        if not isinstance(data, dict):
            continue
        name = key.rsplit(".", 1)[-1] if isinstance(key, str) else str(key)
        pkg = (data.get("package") or "").strip()
        q = symbol_key(pkg, name) if pkg else (key if isinstance(key, str) else name)
        if not q:
            continue
        existing = normalized.get(q) or {}
        # Prefer richer signature when colliding
        keep = data
        if existing:
            if _is_placeholder_signature(data.get("signature", "")) and not _is_placeholder_signature(
                existing.get("signature", "")
            ):
                keep = existing
            elif not _is_placeholder_signature(existing.get("signature", "")) and _is_placeholder_signature(
                data.get("signature", "")
            ):
                keep = existing
        keep = dict(keep)
        keep.setdefault("package", pkg or (q.rsplit(".", 1)[0] if "." in q else ""))
        keep.setdefault("exports", [name])
        normalized[q] = keep
    out["symbols"] = normalized
    return out


def _guess_package_for_symbol(name: str, packages: Dict[str, Any], current_pkg: str | None) -> str:
    if current_pkg and current_pkg in packages:
        return current_pkg
    # Prefer boundary packages for *Handler / *Controller / *Router names
    lower = name.lower()
    if any(lower.endswith(sfx) for sfx in ("handler", "controller", "router", "endpoint", "view")):
        for pkg in packages:
            if package_has_boundary_keywords(pkg, packages.get(pkg, {}).get("owns") if isinstance(packages.get(pkg), dict) else None):
                return pkg
    if packages:
        return next(iter(sorted(packages.keys())))
    return current_pkg or ""


def extract_planned_interfaces_from_specs(*texts: str, packages: Optional[Dict[str, Any]] = None) -> dict:
    """Parse interface/signature prose from design/solution text into planned symbols.

    Language-neutral: accepts Go/Python/TS/Java-style signature lines and package headings.
    Returns ``{"symbols": {...}, "owns_by_package": {pkg: [names...]}}``.
    """
    packages = packages or {}
    symbols: Dict[str, Any] = {}
    owns_by_package: Dict[str, List[str]] = {}
    current_pkg: str | None = None

    for text in texts:
        if not text:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip().strip("`")
            if not line or line.startswith("```"):
                continue

            pkg_match = _PKG_HEADING_RE.match(raw_line.strip())
            if pkg_match:
                current_pkg = next(g for g in pkg_match.groups() if g)
                current_pkg = normalize_workspace_path(current_pkg)
                continue

            # Strip list markers
            line = re.sub(r"^[-*+]\s+", "", line)
            line = re.sub(r"^\d+[.)]\s+", "", line)

            sig_match = _SIG_LINE_RE.search(line)
            if not sig_match:
                continue
            name = sig_match.group(1) or sig_match.group(2)
            if not name:
                continue
            # Keep a cleaned signature span from the line
            sig = line.rstrip("{").strip().rstrip(":")
            if len(sig) > 200:
                sig = sig[:200]

            pkg = _guess_package_for_symbol(name, packages, current_pkg)
            if not pkg:
                continue
            q = symbol_key(pkg, name)
            if not q:
                continue
            if q not in symbols or _is_placeholder_signature(symbols[q].get("signature", "")):
                symbols[q] = {
                    "package": pkg,
                    "signature": sig,
                    "exports": [name],
                }
            owns_by_package.setdefault(pkg, [])
            if name not in owns_by_package[pkg]:
                owns_by_package[pkg].append(name)

    return {"symbols": symbols, "owns_by_package": owns_by_package}


def merge_planned_interfaces_into_contract(contract: dict, planned: dict) -> dict:
    """Soft-merge planned symbols/owns into *contract* without clobbering richer data."""
    if not contract or not planned:
        return contract
    out = json.loads(json.dumps(contract))
    packages = out.setdefault("packages", {})
    symbols = out.setdefault("symbols", {})

    for pkg, names in (planned.get("owns_by_package") or {}).items():
        pkg_data = packages.setdefault(pkg, {"files": [], "owns": []})
        owns = pkg_data.setdefault("owns", [])
        for n in names or []:
            if n not in owns:
                owns.append(n)

    for q, data in (planned.get("symbols") or {}).items():
        if not isinstance(data, dict):
            continue
        existing = symbols.get(q) or {}
        if existing and not _is_placeholder_signature(existing.get("signature", "")):
            continue
        if existing and _is_placeholder_signature(data.get("signature", "")):
            continue
        merged = dict(existing)
        merged.update({k: v for k, v in data.items() if v})
        symbols[q] = merged

    out["symbols"] = symbols
    return normalize_symbol_keys(out)


def strengthen_contract_from_specs(
    contract: dict,
    *spec_texts: str,
) -> dict:
    """Fill owns/symbols from prose when emit was weak; normalize keys; stamp source.

    Skips prose interface merge when ``_meta.source`` is already ``jq-patch`` (structured
    emit won); still repairs weak module names from titles.
    """
    if not contract:
        return contract
    out = normalize_symbol_keys(contract)
    meta_src = (out.get("_meta") or {}).get("source")
    # Do not prose-strengthen over a successful jq-patch (pollutes owns/symbols).
    if meta_src != "jq-patch":
        planned = extract_planned_interfaces_from_specs(*spec_texts, packages=out.get("packages") or {})
        if planned.get("symbols") or planned.get("owns_by_package"):
            out = merge_planned_interfaces_into_contract(out, planned)
            meta = dict(out.get("_meta") or {})
            if meta.get("source") == "extract-fallback" and contract_has_planned_apis(out):
                meta["source"] = "spec-interfaces"
                if out.get("symbols"):
                    meta["enforcement"] = "relaxed"  # prose-derived; soft by default
                out["_meta"] = meta
    # Reject weak module names when specs suggest a better title
    if _is_weak_module_name(out.get("module") or ""):
        better = infer_module_from_specs(
            spec_texts[0] if spec_texts else "",
            spec_texts[1] if len(spec_texts) > 1 else "",
            out.get("packages") or {},
            tech_stack=spec_texts[2] if len(spec_texts) > 2 else None,
        )
        if better and not _is_weak_module_name(better):
            out["module"] = better
    return out


def parse_emitted_wiring_contract(*texts: str) -> Optional[dict]:
    """Parse the first valid <wiring_contract> JSON or fenced wiring JSON from texts."""
    for text in texts:
        if not text or not text.strip():
            continue
        tag_match = re.search(
            r"<wiring_contract>\s*(.*?)\s*</wiring_contract>",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if tag_match:
            try:
                parsed = validate_wiring_contract(json.loads(tag_match.group(1).strip()))
                return stamp_contract_meta(parsed, source="emitted")
            except Exception as exc:
                logger.warning("Failed to parse <wiring_contract> tag: %s", exc)

        for block in re.findall(
            r"```(?:json)?\s*(\{\s*\"version\"\s*:\s*\d+\s*,\s*\"module\"\s*:.*?\})\s*```",
            text,
            re.DOTALL,
        ):
            try:
                data = json.loads(block)
                if isinstance(data.get("packages"), dict):
                    parsed = validate_wiring_contract(data)
                    return stamp_contract_meta(parsed, source="emitted")
            except Exception:
                continue
    return None


def enrich_wiring_contract_from_tldr(
    workspace: Path,
    contract: dict,
    *,
    lang: Optional[str] = None,
) -> dict:
    """Overlay observed symbols/deps from tldr structure + call graph onto *contract*.

    Preserves planned module/packages/files ownership. Fills empty symbols and
    deps from disk when tldr is available. No-ops safely if tldr is missing.
    """
    if not contract:
        return contract

    workspace = Path(workspace)
    enriched = json.loads(json.dumps(contract))  # deep copy via JSON

    try:
        from ..tools.tldr_tools import (
            _resolve_tldr_bin,
            _workspace_has_indexable_source,
            read_call_graph,
            detect_tldr_lang,
        )
    except Exception as exc:
        logger.debug("enrich_wiring_contract_from_tldr: tldr import failed: %s", exc)
        return enriched

    if not _workspace_has_indexable_source(workspace) or not _resolve_tldr_bin():
        return enriched

    detected = lang or detect_tldr_lang(workspace)
    try:
        import subprocess
        tldr_bin = _resolve_tldr_bin()
        result = subprocess.run(
            [tldr_bin, "structure", str(workspace)]
            + (["--lang", detected] if detected else []),
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw = (result.stdout or "").strip()
    except Exception as exc:
        logger.debug("enrich_wiring_contract_from_tldr: structure failed: %s", exc)
        raw = ""

    structure: dict = {}
    if raw:
        try:
            structure = json.loads(raw)
        except json.JSONDecodeError:
            # Non-JSON structure output — skip symbol enrichment
            structure = {}

    packages = enriched.setdefault("packages", {})
    symbols = enriched.setdefault("symbols", {})

    def _pkg_for_path(fp: str) -> Optional[str]:
        return package_for_file(enriched, fp)

    for fentry in structure.get("files") or []:
        fp = (fentry.get("path") or "").replace("\\", "/")
        if not fp:
            continue
        # Make path workspace-relative if absolute
        try:
            fp_path = Path(fp)
            if fp_path.is_absolute():
                fp = str(fp_path.relative_to(workspace)).replace("\\", "/")
        except Exception:
            pass
        pkg = _pkg_for_path(fp)
        if not pkg:
            # Discover package prefix from parent dir if under known roots
            parent = str(Path(fp).parent).replace("\\", "/")
            if parent and parent != ".":
                pkg = parent
                packages.setdefault(pkg, {"files": [], "owns": []})
        if not pkg:
            continue
        pkg_data = packages.setdefault(pkg, {"files": [], "owns": []})
        files = pkg_data.setdefault("files", [])
        if fp not in files:
            files.append(fp)
        owns = pkg_data.setdefault("owns", [])
        for name in (fentry.get("classes") or [])[:12]:
            if name not in owns:
                owns.append(name)
            symbols.setdefault(
                f"{pkg}.{name}",
                {"package": pkg, "signature": name, "exports": [name]},
            )
        for name in ((fentry.get("functions") or []) + (fentry.get("methods") or []))[:20]:
            if name not in owns:
                owns.append(name)
            symbols.setdefault(
                f"{pkg}.{name}",
                {"package": pkg, "signature": name, "exports": [name]},
            )

    # Deps from call graph (package-level)
    deps = enriched.setdefault("deps", [])
    existing = {(d.get("from"), d.get("to")) for d in deps if isinstance(d, dict)}
    try:
        edges = read_call_graph(workspace)
    except Exception:
        edges = []
    for edge in edges[:500]:
        from_file = (edge.get("from_file") or "").replace("\\", "/")
        to_file = (edge.get("to_file") or "").replace("\\", "/")
        if not from_file or not to_file:
            continue
        try:
            if Path(from_file).is_absolute():
                from_file = str(Path(from_file).relative_to(workspace)).replace("\\", "/")
            if Path(to_file).is_absolute():
                to_file = str(Path(to_file).relative_to(workspace)).replace("\\", "/")
        except Exception:
            pass
        from_pkg = _pkg_for_path(from_file)
        to_pkg = _pkg_for_path(to_file)
        if from_pkg and to_pkg and from_pkg != to_pkg:
            key = (from_pkg, to_pkg)
            if key not in existing:
                deps.append({"from": from_pkg, "to": to_pkg})
                existing.add(key)

    try:
        return validate_wiring_contract(enriched)
    except WiringContractError:
        return enriched


def _is_placeholder_signature(sig: str) -> bool:
    """True if signature is missing details (no spaces or no parentheses)."""
    if not sig:
        return True
    return " " not in sig or "(" not in sig


def _normalize_sig(sig: str) -> str:
    """Lightweight normalization for signature comparison — collapses whitespace."""
    import re as _re
    return _re.sub(r"\s+", " ", sig.strip()) if sig else ""


def _wiring_issue_key(issue: dict) -> tuple:
    """Stable key for deduping soft wiring issues across repeated enrich calls."""
    return (
        issue.get("file", ""),
        issue.get("symbol", ""),
        issue.get("type", ""),
        issue.get("description", ""),
    )


def _append_wiring_issue(contract: dict, issue: dict) -> None:
    """Append *issue* to contract['wiring_issues'] if not already present."""
    issues = contract.setdefault("wiring_issues", [])
    key = _wiring_issue_key(issue)
    if any(_wiring_issue_key(existing) == key for existing in issues if isinstance(existing, dict)):
        return
    issues.append(issue)


def _warn_signature_drift(
    file_path: str,
    sym_name: str,
    planned_sig: str,
    observed_sig: str,
) -> dict | None:
    """Log a warning when planned and observed signatures differ (non-trivially).

    Returns a wiring_reconciliation issue dict, or None if sigs are equivalent
    after normalization.
    """
    if _normalize_sig(planned_sig) == _normalize_sig(observed_sig):
        return None  # whitespace/formatting diff only — not worth warning
    logger.warning(
        "[wiring] Signature drift in %s — planned vs observed for '%s':\n"
        "  planned:  %s\n"
        "  observed: %s",
        file_path,
        sym_name,
        planned_sig,
        observed_sig,
    )
    return {
        "file": file_path,
        "symbol": sym_name,
        "description": (
            f"signature drift for '{sym_name}': "
            f"planned='{planned_sig}' observed='{observed_sig}'"
        ),
        "type": "wiring_reconciliation",
        "severity": "error",
    }


def _extract_symbols_from_tldr_data(extract_data: dict) -> list[dict]:
    """Normalize tldr extract payload into a flat list of symbol dicts.

    Real binary (llm-tldr) returns:
        {file_path, language, functions: [...], classes: [...], imports: [...]}
    Each function/class entry has: name, signature, params, return_type, ...

    Tests may mock a simpler shape: {symbols: [...]}

    Returns list of {name, signature, kind}.
    """
    if not extract_data or not isinstance(extract_data, dict):
        return []

    # Compat path: test mocks that use {symbols: [...]}
    if "symbols" in extract_data:
        return [
            {"name": s.get("name", ""), "signature": s.get("signature", ""), "kind": s.get("kind", "function")}
            for s in extract_data["symbols"]
            if s.get("name")
        ]

    result: list[dict] = []
    # Real binary: functions list
    for fn in extract_data.get("functions") or []:
        name = fn.get("name", "")
        if name:
            result.append({
                "name": name,
                "signature": fn.get("signature") or name,
                "kind": "function",
            })
    # Real binary: classes list (may have methods inside)
    for cls in extract_data.get("classes") or []:
        name = cls.get("name", "") if isinstance(cls, dict) else ""
        if name:
            result.append({
                "name": name,
                "signature": cls.get("signature") or f"class {name}",
                "kind": "class",
            })
        for method in cls.get("methods") or [] if isinstance(cls, dict) else []:
            # methods may be plain strings (e.g. ["login", "logout"]) or dicts
            if isinstance(method, str):
                if method:
                    result.append({"name": method, "signature": method, "kind": "method", "class": name})
            elif isinstance(method, dict):
                mname = method.get("name", "")
                if mname:
                    result.append({
                        "name": mname,
                        "signature": method.get("signature") or mname,
                        "kind": "method",
                        "class": name,
                    })
    return result

def enrich_wiring_contract_from_file(
    contract: dict,
    file_path: str,
    extract_data: dict,
    imports_data: list[str],
) -> dict:
    """Enrich wiring contract using per-file tldr extraction (fast)."""
    if not contract:
        return contract

    enriched = json.loads(json.dumps(contract))  # deep copy via JSON

    try:
        # 1. Normalize payload (handles real binary shape + test mock shape)
        observed_symbols = _extract_symbols_from_tldr_data(extract_data)

        if observed_symbols:
            packages = enriched.setdefault("packages", {})
            symbols = enriched.setdefault("symbols", {})

            pkg_name = package_for_file(enriched, file_path)
            if pkg_name:
                pkg_data = packages.setdefault(pkg_name, {"files": [], "owns": []})
                owns = pkg_data.setdefault("owns", [])
                files = pkg_data.setdefault("files", [])

                if file_path not in files:
                    files.append(file_path)

                for sym_data in observed_symbols:
                    sym_name = sym_data.get("name")
                    if not sym_name:
                        continue

                    if sym_name not in owns:
                        owns.append(sym_name)

                    qualified_name = symbol_key(pkg_name, sym_name)
                    existing_sym = symbols.get(qualified_name) or symbols.get(sym_name)
                    if existing_sym is None:
                        existing_sym = {}
                    else:
                        symbols.pop(sym_name, None)
                    existing_sig = existing_sym.get("signature", "")
                    new_sig = sym_data.get("signature") or sym_name

                    if _is_placeholder_signature(existing_sig):
                        sig_to_use = new_sig
                    else:
                        # Soft reconcile: warn + collect issue when planned sig diverges
                        if existing_sig and new_sig:
                            issue = _warn_signature_drift(file_path, qualified_name, existing_sig, new_sig)
                            if issue:
                                _append_wiring_issue(enriched, issue)
                        sig_to_use = existing_sig

                    symbols[qualified_name] = {
                        "package": pkg_name,
                        "signature": sig_to_use,
                        "exports": existing_sym.get("exports") or [sym_name],
                    }

        # 2. Extract imports and add dep edges
        if imports_data:
            deps = enriched.setdefault("deps", [])
            existing_deps = {(d.get("from"), d.get("to")) for d in deps if isinstance(d, dict)}
            
            module_root = import_prefix(enriched)
            from_pkg = package_for_file(enriched, file_path)
            
            if from_pkg:
                for imp in imports_data:
                    if module_root and imp.startswith(module_root + "/"):
                        imp_path = imp[len(module_root) + 1:]
                    else:
                        imp_path = imp
                        
                    to_pkg = package_for_file(enriched, imp_path)
                    if not to_pkg and imp_path in enriched.get("packages", {}):
                        to_pkg = imp_path
                        
                    if to_pkg and from_pkg != to_pkg:
                        edge = (from_pkg, to_pkg)
                        if edge not in existing_deps:
                            deps.append({"from": from_pkg, "to": to_pkg})
                            existing_deps.add(edge)

    except Exception as exc:
        logger.debug("enrich_wiring_contract_from_file failed for %s: %s", file_path, exc)

    return enriched


def validate_wiring_contract(data: dict) -> dict:
    """Raise WiringContractError on invalid; return normalized dict."""
    if not isinstance(data, dict):
        raise WiringContractError("Contract data must be a dictionary.")

    # 1. Check version
    if "version" not in data:
        raise WiringContractError("Missing required field: 'version'")
    try:
        version = int(data["version"])
    except (ValueError, TypeError):
        raise WiringContractError("Field 'version' must be an integer.")

    # 2. Check module
    if "module" not in data:
        raise WiringContractError("Missing required field: 'module'")
    if not isinstance(data["module"], str) or not data["module"].strip():
        raise WiringContractError("Field 'module' must be a non-empty string.")

    # 3. Check packages
    if "packages" not in data:
        raise WiringContractError("Missing required field: 'packages'")
    if not isinstance(data["packages"], dict):
        raise WiringContractError("Field 'packages' must be a dictionary.")

    for pkg_name, pkg_data in data["packages"].items():
        if not isinstance(pkg_name, str) or not pkg_name.strip():
            raise WiringContractError("Package keys must be non-empty strings.")
        if not isinstance(pkg_data, dict):
            raise WiringContractError(f"Package '{pkg_name}' details must be a dictionary.")
        if "files" not in pkg_data:
            raise WiringContractError(f"Package '{pkg_name}' is missing required field: 'files'")
        if not isinstance(pkg_data["files"], list):
            raise WiringContractError(f"Package '{pkg_name}' field 'files' must be a list of strings.")
        for f in pkg_data["files"]:
            if not isinstance(f, str):
                raise WiringContractError(f"File paths in package '{pkg_name}' must be strings.")

        if "owns" in pkg_data:
            if not isinstance(pkg_data["owns"], list):
                raise WiringContractError(f"Package '{pkg_name}' field 'owns' must be a list of strings.")
            for o in pkg_data["owns"]:
                if not isinstance(o, str):
                    raise WiringContractError(f"Owned symbols in package '{pkg_name}' must be strings.")

    # 4. Check symbols (optional)
    if "symbols" in data:
        if not isinstance(data["symbols"], dict):
            raise WiringContractError("Field 'symbols' must be a dictionary.")
        normalized_syms = {}
        for sym_name, sym_data in data["symbols"].items():
            if not isinstance(sym_name, str) or not sym_name.strip():
                raise WiringContractError("Symbol keys must be non-empty strings.")
            if not isinstance(sym_data, dict):
                raise WiringContractError(f"Symbol '{sym_name}' details must be a dictionary.")
            if "package" not in sym_data:
                raise WiringContractError(f"Symbol '{sym_name}' is missing required field: 'package'")
            if not isinstance(sym_data["package"], str):
                raise WiringContractError(f"Symbol '{sym_name}' field 'package' must be a string.")
            if "signature" in sym_data and not isinstance(sym_data["signature"], str):
                raise WiringContractError(f"Symbol '{sym_name}' field 'signature' must be a string.")
            if "exports" in sym_data:
                if not isinstance(sym_data["exports"], list):
                    raise WiringContractError(f"Symbol '{sym_name}' field 'exports' must be a list of strings.")
                for exp in sym_data["exports"]:
                    if not isinstance(exp, str):
                        raise WiringContractError(f"Exports in symbol '{sym_name}' must be strings.")
            pkg = sym_data["package"]
            if pkg and not sym_name.startswith(pkg + "."):
                normalized_syms[f"{pkg}.{sym_name}"] = sym_data
            else:
                normalized_syms[sym_name] = sym_data
        data["symbols"] = normalized_syms

    # 5. Check deps (optional)
    if "deps" in data:
        if not isinstance(data["deps"], list):
            raise WiringContractError("Field 'deps' must be a list.")
        for idx, dep in enumerate(data["deps"]):
            if not isinstance(dep, dict):
                raise WiringContractError(f"Dependency at index {idx} must be a dictionary.")
            if "from" not in dep or "to" not in dep:
                raise WiringContractError(f"Dependency at index {idx} must contain 'from' and 'to' fields.")
            if not isinstance(dep["from"], str) or not isinstance(dep["to"], str):
                raise WiringContractError(f"Dependency fields 'from' and 'to' must be strings.")

    # 6. Check tiers (optional)
    if "tiers" in data:
        if not isinstance(data["tiers"], dict):
            raise WiringContractError("Field 'tiers' must be a dictionary.")
        for tier_name, pkgs in data["tiers"].items():
            if not isinstance(pkgs, list):
                raise WiringContractError(f"Tier '{tier_name}' must be a list of package strings.")
            for p in pkgs:
                if not isinstance(p, str):
                    raise WiringContractError(f"Tier '{tier_name}' packages must be strings.")

    # 7. Check cross_tier (optional)
    if "cross_tier" in data:
        if not isinstance(data["cross_tier"], list):
            raise WiringContractError("Field 'cross_tier' must be a list.")
        for idx, ct in enumerate(data["cross_tier"]):
            if not isinstance(ct, dict):
                raise WiringContractError(f"Cross-tier at index {idx} must be a dictionary.")
            if "from" not in ct or "to" not in ct:
                raise WiringContractError(f"Cross-tier at index {idx} must contain 'from' and 'to' fields.")
            if not isinstance(ct["from"], str) or not isinstance(ct["to"], str):
                raise WiringContractError(f"Cross-tier fields 'from' and 'to' must be strings.")
            if "role" in ct and not isinstance(ct["role"], str):
                raise WiringContractError(f"Cross-tier field 'role' must be a string.")

    if "_meta" in data:
        if not isinstance(data["_meta"], dict):
            raise WiringContractError("Field '_meta' must be a dictionary when present.")

    return data


def write_wiring_contract(workspace: Path, data: dict) -> Path:
    """validate → write workspace/wiring_contract.json (indent=2, trailing newline)."""
    normalized = validate_wiring_contract(data)
    target = Path(workspace) / WIRING_CONTRACT_FILENAME
    content = json.dumps(normalized, indent=2) + "\n"
    target.write_text(content, encoding="utf-8")
    return target


def load_wiring_contract(workspace: Path) -> dict | None:
    """Return validated dict or None if missing/invalid."""
    target = Path(workspace) / WIRING_CONTRACT_FILENAME
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return validate_wiring_contract(data)
    except Exception as exc:
        logger.warning("Failed to load wiring contract from %s: %s", target, exc)
        return None


def all_declared_files(contract: dict) -> set[str]:
    """Union of packages[*].files."""
    files = set()
    packages = contract.get("packages") or {}
    for pkg in packages.values():
        for f in pkg.get("files") or []:
            files.add(f)
    return files


def package_for_file(contract: dict, file_path: str) -> str | None:
    """Find the package in the contract that owns the given file path."""
    if not file_path:
        return None

    # Normalise file path
    fp = file_path.replace("\\", "/").strip().lstrip("/")
    packages = contract.get("packages") or {}

    # 1. Exact file match in packages[*].files
    for pkg_name, pkg_data in packages.items():
        for f in pkg_data.get("files") or []:
            f_norm = f.replace("\\", "/").strip().lstrip("/")
            if fp == f_norm:
                return pkg_name

    # 2. Package prefix match
    # Match the package with the longest matching directory prefix
    best_pkg = None
    best_len = -1
    for pkg_name in packages:
        pkg_prefix = pkg_name.replace("\\", "/").strip().lstrip("/")
        if fp == pkg_prefix or fp.startswith(pkg_prefix + "/"):
            if len(pkg_prefix) > best_len:
                best_len = len(pkg_prefix)
                best_pkg = pkg_name

    return best_pkg


def deps_for_package(contract: dict, package: str) -> list[str]:
    """Outbound deps[].to for this package."""
    outbound = []
    deps = contract.get("deps") or []
    for d in deps:
        if d.get("from") == package:
            outbound.append(d.get("to"))
    return outbound


def slice_for_file(contract: dict, file_path: str, *, max_chars: int = 2500) -> str:
    """Deterministic prompt block: MODULE, package ownership, symbols in package,
    outbound deps + their key symbols/signatures. Truncate to max_chars."""
    if not contract:
        return ""

    module_root = contract.get("module", "")
    pkg_name = package_for_file(contract, file_path)

    parts = []
    parts.append(f"MODULE: {module_root}")
    if pkg_name:
        parts.append(f"CURRENT PACKAGE: {pkg_name}")
        pkg_data = contract.get("packages", {}).get(pkg_name, {})
        owns = pkg_data.get("owns") or []
        if owns:
            parts.append(f"OWNED CONCEPTS / SYMBOLS: {', '.join(owns)}")
        
        # Gather symbols in this package
        pkg_symbols = []
        for sym_name, sym_data in contract.get("symbols", {}).items():
            if sym_data.get("package") == pkg_name:
                sig = sym_data.get("signature") or sym_name
                pkg_symbols.append(f"  - {sig}")
        if pkg_symbols:
            parts.append("DECLARED PACKAGE SIGNATURES:\n" + "\n".join(pkg_symbols))

        # Dependencies
        outbound = deps_for_package(contract, pkg_name)
        if outbound:
            parts.append(f"DEPENDS ON PACKAGES: {', '.join(outbound)}")
            dep_details = []
            for dep_pkg in outbound:
                dep_pkg_data = contract.get("packages", {}).get(dep_pkg, {})
                dep_owns = dep_pkg_data.get("owns") or []
                dep_details.append(f"Package: {dep_pkg}")
                if dep_owns:
                    dep_details.append(f"  Owns: {', '.join(dep_owns)}")
                # Show key signatures in dependency
                dep_syms = []
                for sym_name, sym_data in contract.get("symbols", {}).items():
                    if sym_data.get("package") == dep_pkg:
                        sig = sym_data.get("signature") or sym_name
                        dep_syms.append(f"    - {sig}")
                if dep_syms:
                    dep_details.append("  Signatures:\n" + "\n".join(dep_syms))
            if dep_details:
                parts.append("DEPENDENCY SIGNATURES & CONCEPTS:\n" + "\n".join(dep_details))
    else:
        parts.append(f"Target file `{file_path}` does not map to a specific package prefix in the contract.")

    res = "\n\n".join(parts)
    if len(res) > max_chars:
        res = res[:max_chars] + "\n[... wiring contract slice truncated ...]"
    return res


def format_prompt_section(contract: dict, file_path: str) -> str:
    """Wrapper used by build_file_prompt — header + slice_for_file."""
    w_slice = slice_for_file(contract, file_path)
    if not w_slice:
        return ""
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "WIRING CONTRACT REFERENCE\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{w_slice}\n"
    )


def import_prefix(contract: dict) -> str:
    """Return contract['module'] for prompt PROJECT IDENTITY."""
    return contract.get("module") or ""


# Generic boundary/layer keywords — not tied to any single language layout.
_BOUNDARY_KEYWORDS = (
    "api", "handler", "handlers", "controller", "controllers",
    "route", "routes", "http", "server", "service", "services",
    "view", "views", "router", "endpoint", "endpoints",
)

_SOURCE_SUFFIXES = frozenset({
    ".go", ".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".kt", ".rs", ".rb", ".php", ".cs",
})


def normalize_workspace_path(path: str) -> str:
    """Normalize a workspace-relative path."""
    return path.replace("\\", "/").strip().lstrip("/")


def infer_module_from_specs(
    solution_spec: str,
    design_spec: str,
    packages: Optional[Dict[str, Any]] = None,
    tech_stack: str | None = None,
    workspace: Optional[Path] = None,
) -> str:
    """Best-effort module/import root from manifests, specs or workspace name (language-neutral)."""
    # 1. Try to read workspace manifest files
    if workspace:
        workspace_path = Path(workspace)
        go_mod = workspace_path / "go.mod"
        if go_mod.is_file():
            try:
                for line in go_mod.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("module "):
                        parts = line.strip().split()
                        if len(parts) > 1:
                            candidate = parts[1].strip()
                            if not _is_weak_module_name(candidate):
                                return candidate
            except Exception:
                pass
        pkg_json = workspace_path / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("name"):
                    candidate = data["name"].strip()
                    if not _is_weak_module_name(candidate):
                        return candidate
            except Exception:
                pass
        for pfile in ("pyproject.toml", "Cargo.toml"):
            mfile = workspace_path / pfile
            if mfile.is_file():
                try:
                    for line in mfile.read_text(encoding="utf-8").splitlines():
                        if line.strip().startswith("name ") or line.strip().startswith("name="):
                            m = re.search(r'name\s*=\s*["\']([a-zA-Z0-9_./-]+)["\']', line)
                            if m:
                                candidate = m.group(1).strip()
                                if not _is_weak_module_name(candidate):
                                    return candidate
                except Exception:
                    pass

    # 2. Try to extract header title from specs (e.g. "# Sandbox API" -> "sandbox-api")
    for spec in (solution_spec or "", design_spec or "", tech_stack or ""):
        if not spec:
            continue
        title_match = re.search(r'^#[ \t]+([a-zA-Z0-9_-]+(?:[ \t]+[a-zA-Z0-9_-]+)*)', spec, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip().lower()
            if not any(x in title for x in ("solution", "design", "spec", "tech-stack", "architecture", "implementation")):
                candidate = title.replace(" ", "-")
                if not _is_weak_module_name(candidate):
                    return candidate

    # 3. Read specs for module/name declarations
    for spec in (solution_spec or "", design_spec or "", tech_stack or ""):
        if not spec:
            continue
        for pattern, transform in (
            (r'\bmodule\s+([a-zA-Z0-9_./-]+)\b', lambda m: m.group(1)),
            (r'"name"\s*:\s*"([a-zA-Z0-9@/_-]+)"', lambda m: m.group(1)),
            (r'^\s*name\s*=\s*["\']([a-zA-Z0-9_./-]+)["\']', lambda m: m.group(1)),
        ):
            match = re.search(pattern, spec, re.MULTILINE)
            if match:
                candidate = transform(match).strip()
                # Prefer full path when present; else leaf — but skip weak leaves
                if not _is_weak_module_name(candidate):
                    return candidate
                leaf = candidate.split("/")[-1]
                if leaf and not _is_weak_module_name(leaf):
                    return leaf

    # 4. Fallback to package hierarchy, but skip 'cmd'/'src' parent names
    if packages:
        first_pkg = next(iter(sorted(packages.keys())), "")
        parts = [p for p in first_pkg.split("/") if p]
        if parts:
            if parts[0] in ("cmd", "src") and len(parts) > 1:
                if not _is_weak_module_name(parts[1]):
                    return parts[1]
            if not _is_weak_module_name(parts[0]):
                return parts[0]

    # 5. Fallback to workspace directory name
    if workspace:
        name = workspace.name
        if name.startswith("job-") and len(name) > 4:
            # Prefer vision-ish names from packages over opaque job ids
            pass
        elif not _is_weak_module_name(name):
            return name

    return "unknown"


def package_has_boundary_keywords(package_name: str, owns: Optional[List[str]] = None) -> bool:
    """True when a package name or owned symbols suggest HTTP/API boundary ownership."""
    tokens = normalize_workspace_path(package_name).lower().replace("/", " ").replace("_", " ").split()
    text = " ".join(tokens)
    if owns:
        text += " " + " ".join(o.lower() for o in owns)
    return any(kw in text for kw in _BOUNDARY_KEYWORDS)


def unauthorized_package_prefix_for_path(path: str, declared_prefixes: set[str]) -> Optional[str]:
    """Return parent package dir when *path* is not under any declared prefix."""
    p_norm = normalize_workspace_path(path)
    if not p_norm or "/" not in p_norm:
        return None
    for prefix in sorted(declared_prefixes, key=len, reverse=True):
        if p_norm == prefix or p_norm.startswith(prefix + "/"):
            return None
    parent = str(Path(p_norm).parent).replace("\\", "/")
    return parent if parent and parent != "." else None


def extract_import_paths(content: str, suffix: str) -> List[str]:
    """Extract import/module paths from source (language-neutral)."""
    imports: List[str] = []
    if suffix == ".go":
        for block in re.findall(r"import\s*\((.*?)\)", content, re.DOTALL):
            imports.extend(re.findall(r'"([^"]+)"', block))
        imports.extend(re.findall(r'import\s+"([^"]+)"', content))
    elif suffix == ".py":
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("from ") and " import " in stripped:
                imports.append(stripped.split()[1])
            elif stripped.startswith("import "):
                imports.append(stripped.split()[1].split(",")[0].strip())
    elif suffix in (".ts", ".tsx", ".js", ".jsx"):
        imports.extend(re.findall(r"""from\s+['"]([^'"]+)['"]""", content))
        imports.extend(re.findall(r"""import\s+['"]([^'"]+)['"]""", content))
        imports.extend(re.findall(r"""require\(\s*['"]([^'"]+)['"]\s*\)""", content))
    elif suffix == ".java":
        imports.extend(re.findall(r"import\s+([\w.]+)\s*;", content))
    return [i.strip() for i in imports if i.strip()]


def import_references_declared_package(imp: str, declared_prefixes: set[str]) -> bool:
    """True when an import path appears to reference a declared package."""
    if not imp or imp.startswith("."):
        return False
    imp_path = imp.replace(".", "/")
    for dp in declared_prefixes:
        norm = normalize_workspace_path(dp)
        if not norm:
            continue
        dotted = norm.replace("/", ".")
        if imp_path == norm or imp_path.endswith("/" + norm):
            return True
        if imp == dotted or imp.endswith("." + dotted):
            return True
    return False


def import_matches_module_root(imp: str, module_root: str) -> bool:
    """True when a local import uses the canonical module root (or is relative)."""
    if not imp:
        return True
    if imp.startswith((".", "/")):
        return True
    if not module_root:
        return True
    candidates = {
        module_root,
        module_root.replace("/", "."),
        module_root.replace(".", "/"),
    }
    for root in candidates:
        if imp == root or imp.startswith(root + "/") or imp.startswith(root + "."):
            return True
    return False


def _directory_has_source_files(directory: Path) -> bool:
    for p in directory.rglob("*"):
        if p.is_file() and p.suffix in _SOURCE_SUFFIXES:
            return True
    return False


def collect_unauthorized_sibling_packages(
    workspace: Path,
    declared_prefixes: set[str],
) -> List[str]:
    """Find on-disk package dirs that are siblings of declared packages but not in the contract."""
    parents: Dict[str, Set[str]] = {}
    for prefix in declared_prefixes:
        norm = normalize_workspace_path(prefix)
        parent = str(Path(norm).parent).replace("\\", "/")
        if parent == ".":
            continue
        parents.setdefault(parent, set()).add(norm)

    unauthorized: List[str] = []
    workspace = Path(workspace)
    for parent, known in parents.items():
        parent_path = workspace / parent
        if not parent_path.is_dir():
            continue
        for child in sorted(parent_path.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            child_prefix = normalize_workspace_path(f"{parent}/{child.name}")
            if child_prefix in declared_prefixes:
                continue
            if _directory_has_source_files(child):
                unauthorized.append(child_prefix)
    return unauthorized


def reconcile_workspace_against_contract(
    contract: dict,
    workspace: Path,
) -> List[Dict[str, str]]:
    """Language-neutral wiring drift checks; returns actionable issue dicts."""
    issues: List[Dict[str, str]] = []
    if not contract:
        return issues

    workspace = Path(workspace)
    module_root = contract.get("module") or ""
    packages = contract.get("packages") or {}
    declared_prefixes = {normalize_workspace_path(p) for p in packages.keys()}

    for pkg_dir in collect_unauthorized_sibling_packages(workspace, declared_prefixes):
        issues.append({
            "file": pkg_dir,
            "description": (
                f"unauthorized package: '{pkg_dir}' found on disk but not in contract.packages"
            ),
            "type": "wiring_reconciliation",
        })

    for p in sorted(workspace.rglob("*")):
        if not p.is_file() or p.suffix not in _SOURCE_SUFFIXES:
            continue
        rel_path = normalize_workspace_path(str(p.relative_to(workspace)))
        if rel_path.startswith(".") or rel_path.startswith("tests/") or rel_path.startswith("test/"):
            continue
        if "node_modules" in rel_path or "venv" in rel_path:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if not module_root:
            continue
        for imp in extract_import_paths(content, p.suffix):
            if import_references_declared_package(imp, declared_prefixes):
                if not import_matches_module_root(imp, module_root):
                    issues.append({
                        "file": rel_path,
                        "description": (
                            f"wrong module import prefix: found '{imp}' but expected "
                            f"local prefix matching '{module_root}'"
                        ),
                        "type": "wiring_reconciliation",
                    })
                    break

    return issues


def tech_stack_violates_contract(
    contract: dict,
    tech_stack: str,
    declared_paths: list[str],
    *,
    strict: bool | None = None,
) -> str | None:
    """Return human-readable violation or None.

    Relaxed (default): only flag competing handler/API packages.
    Strict: also require every tech_stack path to sit under declared package prefixes.
    """
    if not contract or not declared_paths:
        return None

    if strict is None:
        strict = is_strict_wiring_enforcement(contract)

    packages = contract.get("packages") or {}
    declared_prefixes = {normalize_workspace_path(p) for p in packages.keys()}
    
    # Always-ok paths / patterns / filenames (generic)
    always_ok_files = {
        "api_contract.yaml", "tech_stack.md", "implementation_plan.md", 
        "design_spec.md", "solution_spec.md", "user_stories.md", 
        "wiring_contract.json", ".gitignore", "README.md", "README"
    }
    
    always_ok_prefixes = {
        ".github/", "systemd/", "test/", "tests/", "docs/", "tests_unit/", 
        "tests_integration/", "tests_e2e/", "test-fixtures/"
    }

    def is_always_ok(path_str: str) -> bool:
        p_norm = normalize_workspace_path(path_str)
        # If in root directory (no slashes), always allowed for generic config files
        if "/" not in p_norm:
            return True
        if p_norm in always_ok_files or Path(p_norm).name in always_ok_files:
            return True
        for prefix in always_ok_prefixes:
            if p_norm.startswith(prefix):
                return True
        return False

    # Competing boundary packages: sibling dirs with similar concern but not in contract
    boundary_packages = [
        pkg_name
        for pkg_name, pkg_data in packages.items()
        if package_has_boundary_keywords(pkg_name, pkg_data.get("owns") or [])
    ]

    if boundary_packages:
        for path in declared_paths:
            if is_always_ok(path):
                continue
            candidate = unauthorized_package_prefix_for_path(path, declared_prefixes)
            if not candidate or candidate in packages:
                continue
            if package_has_boundary_keywords(candidate, []):
                return (
                    f"Competing package '{candidate}' was introduced in tech stack, "
                    f"but '{boundary_packages[0]}' is already the approved package for that concern."
                )

    if not strict:
        return None

    # Strict only — every path must be under a declared prefix or be explicitly declared
    for path in declared_paths:
        p_norm = normalize_workspace_path(path)
        if not p_norm:
            continue
        if is_always_ok(p_norm):
            continue

        # Check explicit file declarations
        is_declared_file = False
        for pkg_data in packages.values():
            for f in pkg_data.get("files") or []:
                f_norm = f.replace("\\", "/").strip().lstrip("/")
                if p_norm == f_norm:
                    is_declared_file = True
                    break
            if is_declared_file:
                break
        
        if is_declared_file:
            continue

        # Check directory prefix match
        has_matching_prefix = False
        for prefix in declared_prefixes:
            if p_norm == prefix or p_norm.startswith(prefix + "/"):
                has_matching_prefix = True
                break

        if not has_matching_prefix:
            return (
                f"Path '{path}' in tech stack is not allowed. "
                f"It must belong to one of the approved package prefixes: {list(declared_prefixes)}."
            )

    return None


def _has_source_suffix(p: str) -> bool:
    """True when path ends with a known source-language suffix (not pkg.Symbol)."""
    return Path(p).suffix.lower() in _SOURCE_SUFFIXES


def _collect_paths_from_spec_text(text: str) -> set[str]:
    """Collect workspace-relative file paths from free text + unicode trees."""
    paths: set[str] = set()
    if not text:
        return paths

    # Contiguous path tokens (bullet lists, prose)
    for line in text.splitlines():
        matches = re.findall(r"\b(?:[a-zA-Z0-9_-]+/)+[a-zA-Z0-9_-]+\.[a-zA-Z0-9]+\b", line)
        for m in matches:
            if not any(x in m for x in [".github", "github.com", "HTTP", "http"]):
                if _is_valid_file_path(m) and _has_source_suffix(m):
                    paths.add(m)

    # Unicode trees / plain one-path-per-line (same parser as task registration)
    for entry in extract_files_with_descriptions_from_tech_stack(text):
        p = (entry.get("path") or "").strip()
        if p and not any(x in p for x in [".github", "github.com", "HTTP", "http"]):
            if _is_valid_file_path(p) and _has_source_suffix(p):
                paths.add(p)

    return paths


def _build_path_only_contract_from_specs(
    solution_spec: str,
    design_spec: str,
    *,
    language_hint: str | None = None,
    tech_stack: str | None = None,
    workspace: Optional[Path] = None,
) -> dict:
    """Language-agnostic path-only contract seed (no symbols/deps)."""
    paths: set[str] = set()
    for spec in (solution_spec, design_spec, tech_stack):
        paths |= _collect_paths_from_spec_text(spec or "")

    packages: Dict[str, Any] = {}
    for p in sorted(paths):
        parent_dir = str(Path(p).parent).replace("\\", "/")
        if parent_dir and parent_dir != ".":
            packages.setdefault(parent_dir, {"files": [], "owns": []})
            if p not in packages[parent_dir]["files"]:
                packages[parent_dir]["files"].append(p)

    # Safety net: root-level files alone must still produce a non-empty packages map
    # so locking does not skip write_wiring_contract.
    if not packages and paths:
        packages["."] = {"files": list(sorted(paths)), "owns": []}

    module_name = infer_module_from_specs(solution_spec, design_spec, packages, tech_stack=tech_stack, workspace=workspace)

    return stamp_contract_meta(
        {
            "version": 1,
            "module": module_name,
            "language": language_hint or "unknown",
            "packages": packages,
            "symbols": {},
            "deps": [],
        },
        source="extract-fallback",
        enforcement="relaxed",
    )


def extract_wiring_contract_from_specs(
    solution_spec: str,
    design_spec: str,
    *,
    language_hint: str | None = None,
    tech_stack: str | None = None,
    workspace: Optional[Path] = None,
) -> dict:
    """Resolve contract: full JSON emit > jq patch on path seed > path-only + prose interfaces."""
    emitted = parse_emitted_wiring_contract(solution_spec or "", design_spec or "", tech_stack or "")
    if emitted:
        if language_hint and not emitted.get("language"):
            emitted["language"] = language_hint
        return normalize_symbol_keys(emitted)

    seed = _build_path_only_contract_from_specs(
        solution_spec or "",
        design_spec or "",
        language_hint=language_hint,
        tech_stack=tech_stack,
        workspace=workspace,
    )

    patch = parse_emitted_wiring_patch(design_spec or "", solution_spec or "", tech_stack or "")
    if patch:
        patched = apply_wiring_patch(seed, patch)
        if patched:
            if language_hint and not patched.get("language"):
                patched["language"] = language_hint
            return normalize_symbol_keys(patched)
        logger.warning("wiring jq patch invalid; strengthening path-only seed from prose")

    return strengthen_contract_from_specs(
        seed,
        design_spec or "",
        solution_spec or "",
        tech_stack or "",
    )
