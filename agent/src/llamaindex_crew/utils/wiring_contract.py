"""
Wiring Contract binding utils for LlamaIndex Crew.
Defines schema, validation, loading, writing, slice_for_file, and validation gate logic.

---------------------------------------------------------------------------
NOTE — Module / import-root identity (language-neutral policy + adapters)
---------------------------------------------------------------------------
Policy (applies to every language):
  - One canonical project identity in wiring_contract.module
  - Never use bare layer names (api, src, service, cmd, …) as that root
  - Prefer on-disk package-manager identity when present; else infer from
    vision / title / specs
  - The same string must drive PROJECT IDENTITY prompts and local imports

Adapters (optional quality hooks — missing adapter ≠ unsupported language):
  1. Manifest reader  → read_package_manifest_identity()
  2. Early write order → _sort_manifest_entries() (identity file first)
  3. Post-write sync  → SoftwareDevWorkflow._enrich_wiring_after_file()
  4. Optional smoke   → code_validator compile gate (go tidy/build, tsc, …)

To add a language: implement those four hooks for its package manifest.
Stacks with no package system (e.g. static HTML) keep language hint only
and fall back to vision/title for module — they are not blocked.
---------------------------------------------------------------------------
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, TypedDict

logger = logging.getLogger(__name__)

WIRING_CONTRACT_FILENAME = "wiring_contract.json"

# Injected into solution architect / designer prompts — compact jq patches, not full JSON.
# Concrete <wiring_patch> examples are injected at runtime via wiring_prompt.compose_*
# (stack/skills override) — keep this block language-neutral so it cannot fight Frappe/Go.
WIRING_PATCH_EMIT_INSTRUCTIONS = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REQUIRED — WIRING PATCH (jq program, token-efficient)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The pipeline seeds packages/files from your paths. You MUST also emit a short jq filter
(NOT shell) that sets module, language, package ownership, key signatures, and deps.

Follow FRAMEWORK SKILLS when injected — they are AUTHORITATIVE for folders, files,
and wiring_patch paths. Do not invent a competing language layout from memory.

Rules (language-neutral):
- jq filter syntax only (chains with |). Do NOT emit shell.
- .module = canonical import/package root for the WHOLE project (never bare layer names
  like "api", "service", "cmd", "src", "app", "app_name").
- Never leave the placeholder token app_name in any path.
- .language = primary language (go|python|typescript|javascript|java|rust|…).
- Always set .packages["<pkg>"].files to concrete source paths (with extensions). Owns alone
  are not enough — the pipeline registers file_creation tasks from .files.
- One package per concern — no parallel packages for the same ownership (api vs handlers).
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
- .packages["<pkg>"].files for each important package (concrete paths with extensions)
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


# Checked only outside string literals — naive "def " matching rejected Python
# signatures embedded in .symbols[*].signature (e.g. "def add(self, a, b)").
_FORBIDDEN_PATCH_SUBSTRINGS = (
    "input_filename",
    "@json:",
    "$env",
    "env(",
    "debug(",
    "include ",
)
# jq-only constructs (after string scrub). Avoid bare "def "/"import " which
# appear in language signatures inside JSON string values.
_FORBIDDEN_JQ_CONSTRUCT_RES = (
    re.compile(r"(?m)^\s*def\s+\w+\s*[(:]"),   # jq user-defined function
    re.compile(r"(?m)^\s*import\s+"),
    re.compile(r"(?m)^\s*include\s+"),
    re.compile(r"\btry\s*;"),
    re.compile(r"\bcatch\s*"),
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
    """Strip fences, dedent lines, and ensure the program is a jq filter starting with '.'"""
    program = patch.strip()
    program = re.sub(r"^```(?:jq)?\s*", "", program, flags=re.IGNORECASE)
    program = re.sub(r"\s*```$", "", program)
    # LLMs often indent continuation lines; jq tolerates whitespace but keep it tidy
    program = "\n".join(line.strip() for line in program.splitlines() if line.strip())
    program = program.strip()
    if not program:
        return program
    if not program.startswith("."):
        program = "." + program
    return program


def _coerce_wiring_symbol_entries(contract: dict) -> dict:
    """Normalize LLM symbol values to {package, signature, exports} dicts.

    Models often emit ``.symbols["pkg.Name"] = "(int) -> int"`` (a string) instead
    of an object. Without coercion the whole wiring_patch is discarded.
    """
    if not isinstance(contract, dict):
        return contract
    symbols = contract.get("symbols")
    if not isinstance(symbols, dict):
        return contract

    coerced: Dict[str, Any] = {}
    for key, val in symbols.items():
        key_s = str(key).strip()
        if not key_s:
            continue
        if isinstance(val, str):
            pkg = key_s.rsplit(".", 1)[0] if "." in key_s else ""
            leaf = key_s.rsplit(".", 1)[-1]
            coerced[key_s] = {
                "package": pkg,
                "signature": val.strip(),
                "exports": [leaf] if leaf else [],
            }
            continue
        if isinstance(val, dict):
            entry = dict(val)
            if not entry.get("package"):
                entry["package"] = key_s.rsplit(".", 1)[0] if "." in key_s else ""
            if "signature" in entry and not isinstance(entry["signature"], str):
                entry["signature"] = str(entry["signature"])
            if "exports" not in entry:
                leaf = key_s.rsplit(".", 1)[-1]
                entry["exports"] = [leaf] if leaf else []
            coerced[key_s] = entry
            continue
        # Unsupported shape — drop rather than fail the whole patch
        logger.debug("Dropping non-object wiring symbol %r (%s)", key_s, type(val).__name__)
    contract["symbols"] = coerced
    return contract


def _strip_jq_string_literals(program: str) -> str:
    """Replace quoted strings so safety checks ignore embedded language signatures."""
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', program)


def _normalize_jq_deps_assignments(program: str) -> str:
    """Rewrite map-style deps into array-append form LLMs often emit wrongly.

    ``.deps["cli"] = ["calculator"]`` → ``.deps += [{"from":"cli","to":"calculator"}]``
    """
    def _repl(match: re.Match) -> str:
        frm = match.group(1)
        tos = re.findall(r'"([^"]+)"', match.group(2) or "")
        if not tos:
            return match.group(0)
        parts = [
            f'.deps += [{{"from":"{frm}","to":"{to}"}}]'
            for to in tos
        ]
        return " | ".join(parts)

    return re.sub(
        r'\.deps\["([^"]+)"\]\s*=\s*\[([^\]]*)\]',
        _repl,
        program,
    )


def _patch_program_is_safe(patch: str) -> bool:
    """Reject dangerous jq features without false-positive on signature strings."""
    scrubbed = _strip_jq_string_literals(patch)
    lowered = scrubbed.lower()
    if any(token in lowered for token in _FORBIDDEN_PATCH_SUBSTRINGS):
        return False
    return not any(rx.search(scrubbed) for rx in _FORBIDDEN_JQ_CONSTRUCT_RES)


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
    program = _normalize_jq_deps_assignments(program)
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

    # Owns-only patches are common; default missing files=[] then synthesize paths
    # before schema validation so the patch is not discarded.
    if isinstance(parsed, dict):
        packages = parsed.get("packages")
        if isinstance(packages, dict):
            for _pkg, pkg_data in packages.items():
                if isinstance(pkg_data, dict) and "files" not in pkg_data:
                    pkg_data["files"] = []
        parsed = _coerce_wiring_symbol_entries(parsed)
        parsed = ensure_package_file_paths(parsed) or parsed

    try:
        validated = validate_wiring_contract(parsed)
    except WiringContractError as exc:
        logger.warning("jq wiring patch result failed validation: %s", exc)
        return None

    # Prune packages under internal/ that are not referenced in the jq program
    packages = validated.get("packages") or {}
    import re as _re
    referred_pkgs = set(_re.findall(r'\.packages\["([^"]+)"\]', program))
    referred_pkgs.update(_re.findall(r'\.packages\.([a-zA-Z0-9_/.-]+)', program))
    
    pruned_packages = {}
    for pkg_name, pkg_data in packages.items():
        if (
            pkg_name in referred_pkgs
            or not pkg_name.startswith("internal/")
            or pkg_data.get("owns")
            or pkg_name == "cmd" or pkg_name.startswith("cmd/")
        ):
            pruned_packages[pkg_name] = pkg_data
    validated["packages"] = pruned_packages

    enforcement = "strict" if (validated.get("symbols") or validated.get("deps")) else "relaxed"
    return stamp_contract_meta(
        normalize_signatures_for_language(validated),
        source="jq-patch",
        enforcement=enforcement,
    )


# ── Planned-emit helpers (language-neutral) ─────────────────────────────────

_WEAK_MODULE_NAMES = frozenset({
    # layout roots
    "cmd", "src", "app", "app_name", "main", "pkg", "lib", "internal", "unknown",
    "project", "root", "code", "backend", "frontend",
    # bare layer / concern names — never a whole-project import root
    "api", "apis", "server", "servers", "service", "services", "sandbox",
    "handler", "handlers", "router", "http", "web", "client", "clients",
    "core", "domain", "manager", "engine", "runtime", "worker", "workers",
    "config", "configs", "model", "models", "util", "utils", "common",
    "shared", "test", "tests", "bin", "tools", "scripts",
})


def _is_weak_module_name(name: str) -> bool:
    """True for empty, layout-only, or bare layer names (language-neutral).

    Full import roots like ``github.com/acme/sandbox-api`` or ``@acme/billing``
    are strong even if a path segment looks like a layer keyword.
    """
    n = (name or "").strip().lower().rstrip("/")
    if not n or n == "unknown":
        return True
    # Scoped npm packages: @scope/name
    if n.startswith("@") and "/" in n:
        leaf = n.split("/")[-1]
        return leaf in _WEAK_MODULE_NAMES
    if "/" in n or "." in n:
        # Multi-segment or dotted module paths are strong unless the *entire*
        # path is a weak single segment (already handled) or only 1 segment
        # with a separator artifact.
        parts = [p for p in re.split(r"[/.]", n) if p and p != "@"]
        if len(parts) >= 2:
            return False
        return parts[0] in _WEAK_MODULE_NAMES if parts else True
    return n in _WEAK_MODULE_NAMES


def _stack_mentions_frappe(
    *texts: str,
    stack_manifest: Optional[dict] = None,
) -> bool:
    if isinstance(stack_manifest, dict):
        for key in ("chosen_stack", "explicit_technologies"):
            vals = stack_manifest.get(key) or []
            if isinstance(vals, list) and any(
                "frappe" in str(v).lower() or "erpnext" in str(v).lower() for v in vals
            ):
                return True
        sq = str(stack_manifest.get("skills_query") or "").lower()
        if "frappe" in sq or "erpnext" in sq:
            return True
    blob = " ".join(t or "" for t in texts).lower()
    return any(tok in blob for tok in ("frappe", "erpnext", "doctype"))


def _rewrite_app_name_placeholder(text: str, slug: str) -> str:
    if not text or "app_name" not in text:
        return text
    return re.sub(r"(^|/)app_name(?=/|$)", rf"\1{slug}", text)


def _infer_frappe_app_slug_from_contract(
    contract: dict,
    *texts: str,
) -> str:
    from .wiring_prompt import infer_app_slug

    packages = contract.get("packages") or {}
    skip = {
        "app_name", "api", "src", "internal", "integrations", "js", "web",
        "pos", "services", "tests", "test", "cmd", "docs", "static", "helm",
        "scripts", "features",
    }
    for key in packages:
        parts = str(key).replace("\\", "/").split("/")
        if parts and parts[0] not in skip and re.match(r"^[a-z][a-z0-9_]*$", parts[0]):
            if parts[0] != "app_name":
                return parts[0]
    vision = texts[0] if texts else ""
    return infer_app_slug(vision or " ".join(texts), fallback="frappe_app")


def normalize_frappe_wiring_contract(
    contract: dict | None,
    *,
    vision: str = "",
    solution_spec: str = "",
    design_spec: str = "",
    tech_stack: str = "",
    stack_manifest: Optional[dict] = None,
) -> dict | None:
    """Rewrite placeholder Frappe contracts (app_name / Go modules / internal/).

    Safety net when LLM copies generic wiring examples onto a Frappe stack.
    """
    if not contract or not isinstance(contract, dict):
        return contract
    if not _stack_mentions_frappe(
        vision, solution_spec, design_spec, tech_stack,
        stack_manifest=stack_manifest,
    ):
        return contract

    out = json.loads(json.dumps(contract))
    slug = _infer_frappe_app_slug_from_contract(
        out, vision, solution_spec, design_spec, tech_stack,
    )
    mod = (out.get("module") or "").strip()
    mod_l = mod.lower()
    if (
        not mod
        or mod_l in ("app_name", "unknown")
        or "github.com/" in mod_l
        or mod_l.startswith("github.com")
        or _is_weak_module_name(mod)
    ):
        logger.info("Frappe wiring: replacing module %r with %r", mod, slug)
        out["module"] = slug
    out["language"] = "python"

    drop_prefixes = ("internal/", "cmd/")
    new_packages: Dict[str, Any] = {}
    for key, pkg in (out.get("packages") or {}).items():
        nk = _rewrite_app_name_placeholder(str(key), slug)
        if nk.startswith(drop_prefixes) or nk in ("internal", "cmd"):
            logger.info("Frappe wiring: dropping non-Frappe package %r", key)
            continue
        if not isinstance(pkg, dict):
            continue
        pkg_out = dict(pkg)
        files = []
        for f in pkg_out.get("files") or []:
            nf = _rewrite_app_name_placeholder(str(f), slug)
            if nf.startswith(drop_prefixes):
                continue
            files.append(nf)
        pkg_out["files"] = files
        new_packages[nk] = pkg_out

    # Prefer whatever scaffolding layout is already declared (flat or nested).
    # Agents commonly emit flat ``{slug}/hooks.py``; forcing nested on top causes
    # wiring_reconciliation false failures when nested paths are never written.
    flat_root = slug
    nested_root = f"{slug}/{slug}"
    all_files = {
        f
        for pkg in new_packages.values()
        for f in (pkg.get("files") or [])
        if isinstance(f, str)
    }
    has_flat_scaffold = any(
        f == f"{flat_root}/hooks.py" or f == f"{flat_root}/modules.txt"
        for f in all_files
    )
    has_nested_scaffold = any(
        f.startswith(f"{nested_root}/") and f.endswith(("hooks.py", "modules.txt", "__init__.py"))
        for f in all_files
    )
    if has_flat_scaffold and not has_nested_scaffold:
        scaffold_root = flat_root
        # Drop empty nested package stub if present without scaffolding files
        nested_pkg = new_packages.get(nested_root)
        if isinstance(nested_pkg, dict):
            nested_files = [
                f for f in (nested_pkg.get("files") or [])
                if isinstance(f, str) and f.startswith(f"{nested_root}/")
            ]
            if not nested_files:
                new_packages.pop(nested_root, None)
    elif has_nested_scaffold:
        scaffold_root = nested_root
    else:
        # Match common greenfield / skill-scaffolded app-root layout
        scaffold_root = flat_root

    pkg = new_packages.setdefault(scaffold_root, {"files": [], "owns": []})
    if not isinstance(pkg.get("files"), list):
        pkg["files"] = []
    for required in (
        f"{scaffold_root}/hooks.py",
        f"{scaffold_root}/modules.txt",
        f"{scaffold_root}/__init__.py",
    ):
        if required not in pkg["files"]:
            pkg["files"].append(required)
    if "owns" not in pkg or not isinstance(pkg["owns"], list):
        pkg["owns"] = []
    if "hooks" not in pkg["owns"]:
        pkg["owns"].append("hooks")
    new_packages[scaffold_root] = pkg

    # When flat is canonical, strip nested scaffold declarations so reconciliation
    # does not demand movie_ticketing/movie_ticketing/hooks.py.
    if scaffold_root == flat_root and nested_root in new_packages:
        nested_pkg = new_packages.get(nested_root) or {}
        nested_files = [
            f for f in (nested_pkg.get("files") or [])
            if isinstance(f, str)
            and not f.endswith(("hooks.py", "modules.txt"))
            and f != f"{nested_root}/__init__.py"
        ]
        if nested_files:
            nested_pkg = dict(nested_pkg)
            nested_pkg["files"] = nested_files
            new_packages[nested_root] = nested_pkg
        else:
            new_packages.pop(nested_root, None)

    out["packages"] = new_packages

    # Rewrite symbols / deps keys that still contain app_name
    symbols = out.get("symbols")
    if isinstance(symbols, dict):
        new_syms: Dict[str, Any] = {}
        for sk, sv in symbols.items():
            nsk = _rewrite_app_name_placeholder(str(sk), slug)
            if isinstance(sv, dict):
                sv = dict(sv)
                if "package" in sv:
                    sv["package"] = _rewrite_app_name_placeholder(str(sv["package"]), slug)
            new_syms[nsk] = sv
        out["symbols"] = new_syms
    deps = out.get("deps")
    if isinstance(deps, list):
        new_deps = []
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            d = dict(dep)
            d["from"] = _rewrite_app_name_placeholder(str(d.get("from") or ""), slug)
            d["to"] = _rewrite_app_name_placeholder(str(d.get("to") or ""), slug)
            if d["from"].startswith(drop_prefixes) or d["to"].startswith(drop_prefixes):
                continue
            new_deps.append(d)
        out["deps"] = new_deps

    return out


_MANIFEST_LANGUAGE_HINTS: tuple[tuple[str, str], ...] = (
    ("go.mod", "go"),
    ("package.json", "javascript"),
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("Cargo.toml", "rust"),
    ("build.sbt", "scala"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
    ("composer.json", "php"),
    ("Gemfile", "ruby"),
)


def _parse_sbt_identity(text: str) -> Optional[str]:
    """Extract Scala/sbt project identity: prefer organization, else name."""
    org = None
    name = None
    for line in text.splitlines():
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        m_org = re.search(
            r"(?:ThisBuild\s*/\s*)?organization\s*:=\s*[\"']([a-zA-Z0-9_.-]+)[\"']",
            stripped,
        )
        if m_org:
            org = m_org.group(1).strip()
        m_name = re.search(
            r"(?:ThisBuild\s*/\s*)?name\s*:=\s*[\"']([a-zA-Z0-9_.-]+)[\"']",
            stripped,
        )
        if m_name:
            name = m_name.group(1).strip()
    if org and not _is_weak_module_name(org):
        # JVM package root (e.g. com.example) — strongest import identity
        return org
    if name and not _is_weak_module_name(name):
        return name
    return None


def _workspace_has_html_only_surface(workspace: Path) -> bool:
    """True when the project looks like static HTML (no package-manager root)."""
    html_files = list(workspace.glob("*.html")) + list(workspace.glob("*.htm"))
    if not html_files and not (workspace / "index.html").is_file():
        # Also accept a simple public/ or static/ index
        for sub in ("public", "static", "www", "web"):
            if (workspace / sub / "index.html").is_file():
                html_files = [workspace / sub / "index.html"]
                break
    if not html_files and not (workspace / "index.html").is_file():
        return False
    # If a real package manifest exists, HTML is just an asset surface.
    for fname, _lang in _MANIFEST_LANGUAGE_HINTS:
        if (workspace / fname).is_file():
            return False
    return True


def read_package_manifest_identity(workspace: Optional[Path]) -> tuple[Optional[str], Optional[str]]:
    """Read canonical import/package root + language hint from package manifests.

    Language-neutral across ecosystems that *have* a package identity.
    HTML/static sites have no module system — language may be ``html`` with
    ``module=None`` so callers fall back to vision/title inference.

    Returns ``(module_or_None, language_or_None)``.
    """
    if not workspace:
        return None, None
    workspace_path = Path(workspace)
    if not workspace_path.is_dir():
        return None, None

    go_mod = workspace_path / "go.mod"
    if go_mod.is_file():
        try:
            for line in go_mod.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("module "):
                    parts = line.strip().split()
                    if len(parts) > 1:
                        candidate = parts[1].strip()
                        if candidate and not _is_weak_module_name(candidate):
                            return candidate, "go"
        except OSError:
            pass

    pkg_json = workspace_path / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name"):
                candidate = str(data["name"]).strip()
                if candidate and not _is_weak_module_name(candidate):
                    lang = "typescript" if (workspace_path / "tsconfig.json").is_file() else "javascript"
                    return candidate, lang
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    for pfile, lang in (("pyproject.toml", "python"), ("Cargo.toml", "rust")):
        mfile = workspace_path / pfile
        if not mfile.is_file():
            continue
        try:
            for line in mfile.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("name ") or stripped.startswith("name="):
                    m = re.search(r'name\s*=\s*["\']([a-zA-Z0-9_./@-]+)["\']', stripped)
                    if m:
                        candidate = m.group(1).strip()
                        if candidate and not _is_weak_module_name(candidate):
                            return candidate, lang
        except OSError:
            pass

    build_sbt = workspace_path / "build.sbt"
    if build_sbt.is_file():
        try:
            candidate = _parse_sbt_identity(build_sbt.read_text(encoding="utf-8"))
            if candidate:
                return candidate, "scala"
            return None, "scala"
        except OSError:
            pass

    if _workspace_has_html_only_surface(workspace_path):
        # No package manager — identity comes from vision/title, not a manifest.
        return None, "html"

    for fname, lang in _MANIFEST_LANGUAGE_HINTS:
        if (workspace_path / fname).is_file():
            # Manifest present but no strong name parsed — still expose language.
            return None, lang
    return None, None


def sync_module_identity_from_workspace(
    contract: dict,
    workspace: Optional[Path] = None,
    *,
    solution_spec: str = "",
    design_spec: str = "",
    tech_stack: str = "",
) -> dict:
    """Lock contract.module (and language when known) from package manifests / specs.

    Language-independent rule: one canonical import/package root for the project.
    Prefer on-disk package manager identity over LLM-emitted bare layer names.
    """
    if not contract:
        return contract
    out = json.loads(json.dumps(contract))
    current = (out.get("module") or "").strip()
    manifest_mod, manifest_lang = read_package_manifest_identity(workspace)

    if manifest_mod and not _is_weak_module_name(manifest_mod):
        if current != manifest_mod:
            logger.info(
                "Syncing wiring contract.module from package manifest: %r → %r",
                current, manifest_mod,
            )
            out["module"] = manifest_mod
    elif _is_weak_module_name(current):
        better = infer_module_from_specs(
            solution_spec,
            design_spec,
            out.get("packages") or {},
            tech_stack=tech_stack or None,
            workspace=workspace,
        )
        if better and not _is_weak_module_name(better):
            logger.info(
                "Replacing weak wiring contract.module %r with inferred %r",
                current, better,
            )
            out["module"] = better

    lang = (out.get("language") or "").strip().lower()
    if (not lang or lang in ("unknown", "none", "null")) and manifest_lang:
        out["language"] = manifest_lang

    return out


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
        if not fp or not _has_source_suffix(fp):
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
    """Normalize signature for comparison, removing keyword prefixes, Go receivers, return types, and package qualifiers."""
    if not sig:
        return ""
    import re as _re
    s = sig.strip()
    s = _re.sub(r"\s+", " ", s)
    # Strip Go/Python/TS keywords
    s = _re.sub(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:func|def|function|fn|sub)\s+", "", s)
    # Strip Go receiver
    s = _re.sub(r"^\([^)]+\)\s*", "", s)
    # Extract only Name(...) block
    m = _re.match(r"^([A-Za-z0-9_]+\([^)]*\))", s)
    if m:
        s = m.group(1)
    # Strip package qualifiers from types: e.g. config.Config -> Config
    s = _re.sub(r"\b[A-Za-z0-9_]+\.([A-Za-z0-9_]+)\b", r"\1", s)
    return s


def _signature_language_mismatch(sig: str, language: str) -> bool:
    """True when signature uses the wrong language keyword for *language*."""
    if not sig or not language:
        return False
    lang = language.strip().lower()
    s = sig.strip()
    if lang in ("go", "golang"):
        return bool(re.match(r"^def\s+", s, re.IGNORECASE))
    if lang in ("python", "py"):
        return bool(re.match(r"^func\s+", s, re.IGNORECASE))
    return False


def normalize_signatures_for_language(contract: dict) -> dict:
    """Rewrite wrong-language keywords on planned signatures (e.g. def→func for Go)."""
    if not contract:
        return contract
    lang = (contract.get("language") or "").strip().lower()
    if not lang:
        return contract
    out = json.loads(json.dumps(contract))
    symbols = out.get("symbols") or {}
    changed = 0
    for key, data in list(symbols.items()):
        if not isinstance(data, dict):
            continue
        sig = (data.get("signature") or "").strip()
        if not sig:
            continue
        new_sig = sig
        if lang in ("go", "golang") and re.match(r"^def\s+", sig, re.IGNORECASE):
            new_sig = re.sub(r"^def\s+", "func ", sig, count=1, flags=re.IGNORECASE)
        elif lang in ("python", "py") and re.match(r"^func\s+", sig, re.IGNORECASE):
            new_sig = re.sub(r"^func\s+", "def ", sig, count=1, flags=re.IGNORECASE)
        if new_sig != sig:
            data = dict(data)
            data["signature"] = new_sig
            symbols[key] = data
            changed += 1
    if changed:
        out["symbols"] = symbols
        logger.info(
            "Normalized %d signature(s) to language=%s keywords",
            changed, lang,
        )
    return out


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
            from collections import Counter
            sym_counts = Counter(s.get("name") for s in observed_symbols if s.get("name"))
            
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

                    is_collision = sym_counts[sym_name] > 1
                    existing_file = existing_sym.get("file")
                    if existing_file and existing_file != file_path:
                        is_collision = True

                    if _is_placeholder_signature(existing_sig):
                        sig_to_use = new_sig
                    elif _signature_language_mismatch(
                        existing_sig, (enriched.get("language") or "")
                    ) and new_sig and not _is_placeholder_signature(new_sig):
                        # Planned sig used wrong language keyword (e.g. def for Go) —
                        # upgrade from observed code instead of thrashing on drift.
                        logger.info(
                            "[wiring] Upgrading wrong-language planned sig for %s: %r → %r",
                            qualified_name, existing_sig, new_sig,
                        )
                        sig_to_use = new_sig
                    else:
                        # Soft reconcile: warn + collect issue when planned sig diverges
                        if existing_sig and new_sig and not is_collision:
                            issue = _warn_signature_drift(file_path, qualified_name, existing_sig, new_sig)
                            if issue:
                                _append_wiring_issue(enriched, issue)
                        sig_to_use = existing_sig

                    symbols[qualified_name] = {
                         "package": pkg_name,
                         "signature": sig_to_use,
                         "exports": existing_sym.get("exports") or [sym_name],
                         "file": file_path,
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
            # Owns-only LLM patches omit files; default empty and let synthesis fill.
            pkg_data["files"] = []
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
    workspace = Path(workspace)
    stack_manifest = None
    manifest_path = workspace / "stack_manifest.json"
    if manifest_path.is_file():
        try:
            stack_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            stack_manifest = None

    def _read(name: str) -> str:
        p = workspace / name
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
        return ""

    data = normalize_frappe_wiring_contract(
        data,
        vision=_read("requirements.md") or _read("user_stories.md"),
        solution_spec=_read("solution_spec.md"),
        design_spec=_read("design_spec.md"),
        tech_stack=_read("tech_stack.md"),
        stack_manifest=stack_manifest if isinstance(stack_manifest, dict) else None,
    ) or data
    synced = sync_module_identity_from_workspace(data, workspace)
    normalized = validate_wiring_contract(normalize_signatures_for_language(synced))
    target = workspace / WIRING_CONTRACT_FILENAME
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
        validated = validate_wiring_contract(data)
        # Keep in-memory view aligned with package manifests when present.
        return sync_module_identity_from_workspace(validated, workspace)
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


def import_prefix(contract: dict, workspace: Optional[Path] = None) -> str:
    """Return canonical module/import root for prompt PROJECT IDENTITY.

    When *workspace* is provided, prefer the on-disk package manager identity
    (go.mod / package.json / pyproject / Cargo.toml) over a weak contract.module.
    """
    if not contract and not workspace:
        return ""
    data = contract or {}
    if workspace is not None:
        data = sync_module_identity_from_workspace(data, workspace)
    return (data.get("module") or "").strip()


# Generic boundary/layer keywords — not tied to any single language layout.
_BOUNDARY_KEYWORDS = (
    "api", "handler", "handlers", "controller", "controllers",
    "route", "routes", "http", "server", "service", "services",
    "view", "views", "router", "endpoint", "endpoints",
)

# Domain / lifecycle packages that must not be duplicated under the same parent
# (e.g. internal/service vs internal/sandbox).
_DOMAIN_KEYWORDS = _BOUNDARY_KEYWORDS + (
    "sandbox", "domain", "core", "manager", "engine", "runtime",
    "business", "application", "podman", "container", "containers",
)

_SOURCE_SUFFIXES = frozenset({
    ".go", ".py", ".ts", ".js", ".tsx", ".jsx", ".java", ".kt", ".scala", ".sc",
    ".rs", ".rb", ".php", ".cs", ".c", ".cpp", ".h", ".hpp",
})

# Web delivery surfaces count as implementation for HTML/CSS (and static) projects.
_WEB_DELIVERY_SUFFIXES = frozenset({".html", ".css", ".svg"})


class FileEntry(TypedDict, total=False):
    """Single row in the creation manifest (contract + supplementary tiers)."""
    path: str
    description: str
    manifest_source: Literal["contract", "supplementary", "injected"]
    tier: Literal["contract", "supplementary", "injected"]


_MANIFEST_CONFIG_NAMES = frozenset({
    "dockerfile", "containerfile", "makefile", "gnumakefile",
    "go.mod", "go.sum", "package.json", "package-lock.json",
    "pom.xml", "build.gradle", "build.gradle.kts", "build.sbt",
    "pyproject.toml", "requirements.txt", "cmakelists.txt", "cargo.toml",
})


def should_skip_contract_reseed_from_tech_stack(contract: dict | None) -> bool:
    """When True, post-TA lock must not re-extract packages/files from tech_stack.md."""
    if not contract:
        return False
    meta = contract.get("_meta") or {}
    return meta.get("source") == "jq-patch"


def _normalize_manifest_path(path: str) -> str:
    return normalize_workspace_path(path or "")


def _is_manifest_source_path(path: str, description: str = "") -> bool:
    """True when a manifest path counts as application source (not config/docs)."""
    if not path or path.lower() == "unknown":
        return False
    desc_upper = (description or "").upper()
    if "[SOURCE]" in desc_upper:
        return True
    if "[CONFIG]" in desc_upper:
        return False
    lower = path.lower().replace("\\", "/")
    if "/test/" in lower or lower.startswith("test/") or lower.startswith("tests/") or "/tests/" in lower:
        return False
    basename = Path(lower).name
    if basename in _MANIFEST_CONFIG_NAMES:
        return False
    if "." not in basename:
        return basename.lower() in EXTENSIONLESS_FILENAMES and basename.lower() not in _MANIFEST_CONFIG_NAMES
    ext = Path(lower).suffix.lower()
    if ext in _SOURCE_SUFFIXES or ext in _WEB_DELIVERY_SUFFIXES:
        return True
    return _is_valid_file_path(path)


def _is_manifest_scaffolding_path(path: str) -> bool:
    if not path:
        return True
    lower = path.lower().replace("\\", "/")
    if lower in _MANIFEST_CONFIG_NAMES:
        return True
    base = Path(lower).name
    if base.startswith(".env") or base in (".gitignore", ".dockerignore", ".editorconfig"):
        return True
    # Docs/config prose — not app HTML/CSS deliverables
    if lower.endswith((".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".example")):
        if Path(lower).suffix.lower() not in _SOURCE_SUFFIXES:
            return True
    # HTML/CSS under docs/ are scaffolding; otherwise they are delivery sources
    if lower.endswith((".html", ".css", ".svg")) and (
        lower.startswith("docs/") or "/docs/" in lower
    ):
        return True
    scaffold_prefixes = (
        ".github/", "docs/", "configuration/", "build/", "prerequisites/",
    )
    return any(lower.startswith(p) for p in scaffold_prefixes)


def _camel_to_snake(name: str) -> str:
    """Convert CamelCase / mixed identifiers to snake_case file stems."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name or "")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = s.replace("-", "_").replace(" ", "_")
    return re.sub(r"_+", "_", s).strip("_").lower()


def _package_dir_for_language(pkg: str, language: str) -> str:
    """Map a package key to a workspace-relative directory."""
    raw = (pkg or "").strip().strip("/")
    if not raw or raw == ".":
        return ""
    lang = (language or "").lower()
    if lang in ("python", "py", "java", "kotlin", "scala") or (
        "." in raw and "/" not in raw
    ):
        # Dotted packages (Python/Java) → filesystem path
        return raw.replace(".", "/")
    return raw.replace("\\", "/")


def _default_ext_for_language(language: str) -> str:
    lang = (language or "").lower()
    return {
        "python": ".py",
        "py": ".py",
        "go": ".go",
        "java": ".java",
        "kotlin": ".kt",
        "scala": ".scala",
        "rust": ".rs",
        "typescript": ".ts",
        "ts": ".ts",
        "javascript": ".js",
        "js": ".js",
        "nodejs": ".js",
        "node": ".js",
        "tsx": ".tsx",
        "jsx": ".jsx",
        "html": ".html",
        "css": ".css",
        "csharp": ".cs",
        "c#": ".cs",
        "ruby": ".rb",
        "php": ".php",
    }.get(lang, ".py" if lang in ("", "unknown") else f".{lang}" if lang.isalpha() else ".txt")


def _synthesize_files_for_empty_package(
    pkg: str,
    pkg_data: dict,
    *,
    language: str,
    module: str,
    symbols: dict,
) -> List[str]:
    """Invent concrete source paths when a package has owns/symbols but no files."""
    owns = [str(o) for o in (pkg_data.get("owns") or []) if o]
    dir_path = _package_dir_for_language(pkg, language)
    if not dir_path:
        mod_dir = _package_dir_for_language(module, language) if module else ""
        dir_path = mod_dir or "app"

    # Prefer explicit symbol.file annotations
    out: List[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        p = _normalize_manifest_path(path)
        if p and _is_valid_file_path(p) and p not in seen:
            seen.add(p)
            out.append(p)

    for sym in (symbols or {}).values():
        if not isinstance(sym, dict):
            continue
        sym_pkg = sym.get("package") or ""
        if sym_pkg and sym_pkg != pkg:
            continue
        f = sym.get("file")
        if f:
            _add(str(f))

    lang = (language or "").lower()
    ext = _default_ext_for_language(lang)
    lower_pkg = pkg.lower().replace("\\", "/")

    # Test packages: one file per test_* own, else a default test module
    if (
        lower_pkg == "tests"
        or lower_pkg.endswith("/tests")
        or lower_pkg.endswith(".tests")
        or "/test" in lower_pkg
    ):
        test_owns = [o for o in owns if o.split(".")[-1].startswith("test_")]
        if test_owns:
            for own in test_owns[:6]:
                leaf = _camel_to_snake(own.split(".")[-1])
                _add(f"{dir_path}/{leaf}{ext}")
        else:
            _add(
                f"{dir_path}/test_app{ext}"
                if lang in ("python", "py", "", "unknown")
                else f"{dir_path}/app_test{ext}"
            )
        if lang in ("python", "py", "", "unknown"):
            _add(f"{dir_path}/__init__.py")
        return out

    if lang in ("python", "py", "", "unknown"):
        _add(f"{dir_path}/__init__.py")
        added_body = False
        for own in owns:
            leaf = own.split(".")[-1]
            leaf_l = leaf.lower()
            if leaf_l in ("main", "__init__", "init"):
                _add(f"{dir_path}/main.py")
                added_body = True
                continue
            stem = _camel_to_snake(leaf)
            if not stem or stem.startswith("test_"):
                continue
            _add(f"{dir_path}/{stem}.py")
            added_body = True
            break
        if not added_body:
            base = Path(dir_path).name or "module"
            _add(f"{dir_path}/{_camel_to_snake(base)}.py")
        return out

    if lang == "go":
        base = Path(dir_path).name or "pkg"
        for own in owns:
            leaf = own.split(".")[-1]
            if not leaf:
                continue
            go_stem = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", leaf).lower()
            go_stem = go_stem.split("_")[0] if go_stem else base
            _add(f"{dir_path}/{go_stem}.go")
            break
        if not out:
            _add(f"{dir_path}/{base}.go")
        return out

    if lang in ("html", "css", "svg"):
        rootish = lower_pkg in {
            ".", "web", "static", "public", "frontend", "site", "www",
            (module or "").lower(),
        }
        prefix = "" if rootish else f"{dir_path}/"
        _add(f"{prefix}index.html")
        _add(f"{prefix}styles.css")
        for own in owns:
            leaf = own.split(".")[-1].lower()
            if any(tok in leaf for tok in ("script", "app", "main", "js")):
                _add(f"{prefix}app.js")
                break
        if not any(p.endswith(".js") for p in out):
            _add(f"{prefix}app.js")
        return out

    if lang in ("javascript", "js", "typescript", "ts", "nodejs", "node"):
        use_ext = ".ts" if lang in ("typescript", "ts") else ".js"
        added = False
        for own in owns:
            leaf = own.split(".")[-1]
            leaf_l = leaf.lower()
            stem = "index" if leaf_l in ("main", "index", "app", "server") else _camel_to_snake(leaf)
            if not stem:
                continue
            target = f"{stem}{use_ext}" if dir_path in ("", ".") else f"{dir_path}/{stem}{use_ext}"
            _add(target)
            added = True
            break
        if not added:
            target = f"index{use_ext}" if dir_path in ("", ".") else f"{dir_path}/index{use_ext}"
            _add(target)
        return out

    if lang == "java":
        class_name = None
        for own in owns:
            leaf = own.split(".")[-1]
            if not leaf:
                continue
            if leaf.lower() == "main":
                class_name = "Main"
            elif leaf[0].isupper():
                class_name = leaf
            else:
                class_name = "".join(p.title() for p in _camel_to_snake(leaf).split("_")) or "App"
            break
        if not class_name:
            base = Path(dir_path).name or "App"
            class_name = "".join(p.title() for p in base.replace("-", "_").split("_")) or "App"
        _add(f"{dir_path}/{class_name}.java")
        return out

    # Generic languages: one module file
    base = Path(dir_path).name or "module"
    for own in owns[:1]:
        stem = _camel_to_snake(own.split(".")[-1]) or base
        _add(f"{dir_path}/{stem}{ext}")
    if not out:
        _add(f"{dir_path}/{base}{ext}")
    return out


def ensure_package_file_paths(contract: dict | None) -> dict | None:
    """Fill empty packages[*].files from owns/symbols so manifests get real source paths.

    LLMs often emit wiring_patch owns/symbols without .files. Without files,
    creation-manifest registration only keeps scaffolding/tests and Python/Go
    trees ship empty.
    """
    if not contract or not isinstance(contract, dict):
        return contract
    packages = contract.get("packages")
    if not isinstance(packages, dict) or not packages:
        return contract

    language = str(contract.get("language") or "")
    module = str(contract.get("module") or "")
    symbols = contract.get("symbols") if isinstance(contract.get("symbols"), dict) else {}

    for pkg, pkg_data in list(packages.items()):
        if not isinstance(pkg_data, dict):
            continue
        files = pkg_data.get("files")
        if not isinstance(files, list):
            files = []
            pkg_data["files"] = files
        concrete = [
            _normalize_manifest_path(str(f))
            for f in files
            if f and _is_valid_file_path(_normalize_manifest_path(str(f)))
        ]
        if concrete:
            if concrete != [str(f) for f in files]:
                pkg_data["files"] = concrete
            continue
        owns = pkg_data.get("owns") or []
        has_syms = any(
            isinstance(s, dict) and s.get("package") == pkg
            for s in symbols.values()
        )
        if not owns and not has_syms:
            continue
        synthesized = _synthesize_files_for_empty_package(
            str(pkg),
            pkg_data,
            language=language,
            module=module,
            symbols=symbols,
        )
        if synthesized:
            pkg_data["files"] = synthesized
            logger.info(
                "Synthesized %d file path(s) for empty package %r: %s",
                len(synthesized),
                pkg,
                synthesized[:6],
            )

    return contract


def files_from_contract(wiring_contract: dict | None) -> List[FileEntry]:
    """Mandatory contract-tier paths from wiring_contract.json packages[*].files."""
    if not wiring_contract:
        return []
    wiring_contract = ensure_package_file_paths(wiring_contract) or wiring_contract
    packages = wiring_contract.get("packages") or {}
    entries: List[FileEntry] = []
    seen: set[str] = set()
    for _pkg, pkg_data in packages.items():
        for raw in pkg_data.get("files") or []:
            p = _normalize_manifest_path(str(raw))
            if not p or not _is_valid_file_path(p) or p in seen:
                continue
            seen.add(p)
            entries.append({
                "path": p,
                "description": f"Contract-declared source: {p}",
                "manifest_source": "contract",
                "tier": "contract",
            })
    return entries


def _path_under_contract_packages(path: str, contract: dict) -> bool:
    """True when path sits under a declared contract package prefix or is declared."""
    p_norm = _normalize_manifest_path(path)
    if not p_norm or not contract:
        return False
    packages = contract.get("packages") or {}
    declared_prefixes = {normalize_workspace_path(p) for p in packages.keys()}
    for pkg_data in packages.values():
        for f in pkg_data.get("files") or []:
            if p_norm == _normalize_manifest_path(str(f)):
                return True
    for prefix in declared_prefixes:
        if p_norm == prefix or p_norm.startswith(prefix + "/"):
            return True
    return False


def validate_supplementary_paths(
    supplementary_paths: List[FileEntry],
    contract: dict | None,
) -> List[FileEntry]:
    """Filter Pass 2b / seeder paths — additive only; reject contract collisions."""
    validated: List[FileEntry] = []
    contract_paths = {e["path"] for e in files_from_contract(contract)}
    for raw in supplementary_paths or []:
        p = _normalize_manifest_path(str(raw.get("path") or ""))
        if not p or not _is_valid_file_path(p):
            continue
        if p in contract_paths:
            logger.warning("Supplementary path rejected (contract owns it): %s", p)
            continue
        if contract and _path_under_contract_packages(p, contract):
            # Allow non-source config under contract tree only when not a source file
            if _is_manifest_source_path(p, raw.get("description", "")):
                logger.warning(
                    "Supplementary source path rejected under contract package: %s", p
                )
                continue
        desc = (raw.get("description") or f"Supplementary file: {p}").strip()
        validated.append({
            "path": p,
            "description": desc,
            "manifest_source": "supplementary",
            "tier": "supplementary",
        })
    return validated


def merge_file_manifests(
    mandatory: List[FileEntry],
    supplementary: List[FileEntry],
) -> List[FileEntry]:
    """Union manifests; contract tier wins on path conflict."""
    merged: dict[str, FileEntry] = {}
    for entry in mandatory or []:
        p = _normalize_manifest_path(entry.get("path") or "")
        if p:
            merged[p] = dict(entry)
    for entry in supplementary or []:
        p = _normalize_manifest_path(entry.get("path") or "")
        if not p or p in merged:
            continue
        merged[p] = dict(entry)
    return list(merged.values())


def _inject_init_py_entries(entries: List[FileEntry]) -> List[FileEntry]:
    from ..utils.manifest_guard import expand_python_package_inits

    paths = {e["path"] for e in entries if e.get("path")}
    py_files = [p for p in paths if p.endswith(".py")]
    if not py_files:
        return entries
    needed = {
        p for p in expand_python_package_inits(paths)
        if p.endswith("/__init__.py") and p not in paths
    }
    out = list(entries)
    for init_path in sorted(needed):
        out.append({
            "path": init_path,
            "description": "Python package init (auto-generated)",
            "manifest_source": "injected",
            "tier": "injected",
        })
    return out


def _inject_skill_scaffolding_entries(
    entries: List[FileEntry],
    design_spec: str,
    workspace: Optional[Path],
) -> List[FileEntry]:
    """Add skill-derived structural files not already in the manifest."""
    if not workspace:
        return entries
    prefetch_file = Path(workspace) / "skill_prefetch.json"
    if not prefetch_file.is_file():
        return entries
    skill_paths: set[str] = set()
    try:
        data = json.loads(prefetch_file.read_text(encoding="utf-8"))
        all_entries: list = []
        if isinstance(data, dict):
            for role_entries in data.values():
                if isinstance(role_entries, list):
                    all_entries.extend(role_entries)
        for entry in all_entries:
            content = entry.get("content", "")
            if content:
                for item in extract_files_with_descriptions_from_tech_stack(content):
                    p = (item.get("path") or "").strip()
                    if p and _is_valid_file_path(p):
                        skill_paths.add(p)
    except Exception as exc:
        logger.warning("skill scaffolding inject skipped: %s", exc)
        return entries

    existing = {e["path"] for e in entries if e.get("path")}
    out = list(entries)
    for fp in sorted(skill_paths):
        if fp in existing:
            continue
        if not _is_manifest_source_path(fp):
            continue
        out.append({
            "path": fp,
            "description": "Framework scaffolding file derived from skills",
            "manifest_source": "injected",
            "tier": "injected",
        })
    return out


def _sort_manifest_entries(entries: List[FileEntry]) -> List[FileEntry]:
    """Sort by tier, then package-manager manifests first (sets import root early)."""
    tier_order = {"contract": 0, "supplementary": 1, "injected": 2}
    manifest_basenames = {
        "go.mod", "package.json", "pyproject.toml", "cargo.toml",
        "build.sbt", "pom.xml", "composer.json", "gemfile", "requirements.txt",
    }

    def _key(e: FileEntry) -> tuple:
        tier = e.get("tier") or e.get("manifest_source") or "supplementary"
        path = e.get("path") or ""
        base = Path(path).name.lower()
        # 0 = package identity files first so later source uses the right import root
        identity = 0 if base in manifest_basenames or base.startswith("build.gradle") else 1
        return (tier_order.get(tier, 9), identity, path)

    return sorted(entries, key=_key)


def build_creation_manifest(
    wiring_contract: dict | None,
    supplementary_paths: List[FileEntry],
    design_spec: str,
    *,
    tdd: bool = False,
    workspace: Optional[Path] = None,
) -> List[FileEntry]:
    """Merge contract + supplementary + auto-injected paths into one manifest."""
    _ = tdd  # TDD test paths registered separately via register_tdd_test_tasks
    mandatory = files_from_contract(wiring_contract)
    supplementary = validate_supplementary_paths(supplementary_paths, wiring_contract)
    merged = merge_file_manifests(mandatory, supplementary)
    merged = _inject_init_py_entries(merged)
    merged = _inject_skill_scaffolding_entries(merged, design_spec, workspace)
    return _sort_manifest_entries(merged)


def _implementation_manifest_paths(entries: List[FileEntry]) -> List[str]:
    impl: List[str] = []
    for entry in entries or []:
        path = entry.get("path") or ""
        desc = entry.get("description") or ""
        if not _is_manifest_source_path(path, desc):
            continue
        if _is_manifest_scaffolding_path(path):
            continue
        impl.append(path)
    return impl


def implementation_manifest_paths(entries: List[FileEntry]) -> List[str]:
    """Public alias: concrete implementation source paths in a creation manifest."""
    return _implementation_manifest_paths(entries)


def _adaptive_min_implementation_files(
    components: list,
    *,
    design_spec: str = "",
    solution_spec: str = "",
) -> int:
    """Minimum implementation files — scales with design size, not a hard floor of 4.

    Tiny CLI / calculator visions legitimately have 1–2 source files; multi-service
    designs still require broader coverage.
    """
    n = len(components) if components else 0
    blob = f"{design_spec}\n{solution_spec}".lower()
    simple = any(
        tok in blob
        for tok in (
            "simple",
            "minimal",
            "hello world",
            "command-line",
            "command line",
            "cli calculator",
            "toy ",
            "starter",
        )
    )
    if simple or n <= 2:
        return max(1, n) if n else 1
    if n >= 5:
        return max(4, n)
    if n >= 3:
        return max(3, n)
    return 2


def validate_manifest_completeness(
    entries: List[FileEntry],
    *,
    design_spec: str = "",
    solution_spec: str = "",
) -> Dict[str, Any]:
    """Validate creation manifest has enough concrete implementation files."""
    from ..utils.vision_stack_analysis import (
        component_reflected_in_artifact,
        extract_named_components,
    )

    if not entries:
        return {
            "valid": False,
            "issues": [
                "Creation manifest is empty. Register contract files and/or "
                "supplementary paths before development."
            ],
        }

    all_paths = [e["path"] for e in entries if e.get("path")]
    src_files = [
        e["path"] for e in entries
        if _is_manifest_source_path(e.get("path", ""), e.get("description", ""))
    ]
    impl_files = _implementation_manifest_paths(entries)
    issues: List[str] = []

    if len(src_files) == 0:
        issues.append(
            "No concrete source files in creation manifest. "
            "List real filenames with extensions (e.g. src/main.py, internal/service/manager.go)."
        )

    components = extract_named_components(solution_spec) or extract_named_components(design_spec)
    min_impl = _adaptive_min_implementation_files(
        components,
        design_spec=design_spec,
        solution_spec=solution_spec,
    )
    if len(impl_files) < min_impl:
        issues.append(
            f"Manifest is too shallow: found {len(impl_files)} implementation "
            f"source file(s) but need at least {min_impl} "
            f"(derived from {len(components) or 'default'} named component(s))."
        )

    if components:
        manifest_text = "\n".join(all_paths)
        missing = [
            name for name in components
            if not component_reflected_in_artifact(name, manifest_text, all_paths)
        ]
        if len(missing) >= max(1, (len(components) + 1) // 2):
            issues.append(
                "Manifest does not cover named components from the design/solution "
                f"spec. Missing or unrepresented: {missing[:8]}"
                + ("..." if len(missing) > 8 else "")
            )

    if issues:
        return {"valid": False, "issues": issues}
    return {"valid": True, "issues": [], "implementation_files": len(impl_files)}


def render_file_tree_from_contract(wiring_contract: dict | None) -> str:
    """Deterministic Pass 2a tree from contract package files."""
    entries = files_from_contract(wiring_contract)
    return render_file_tree_from_manifest(entries, root_label="contract/")


def render_file_tree_from_manifest(
    entries: List[FileEntry],
    *,
    root_label: str = "project/",
) -> str:
    """Render a unicode directory tree from manifest paths."""
    paths = sorted({_normalize_manifest_path(e.get("path") or "") for e in entries if e.get("path")})
    if not paths:
        return f"{root_label}\n└── (no files registered)\n"

    tree: dict = {}
    for path in paths:
        parts = path.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node[parts[-1]] = None

    lines: List[str] = [root_label.rstrip("/") + "/"]

    def _walk(node: dict, prefix: str = "") -> None:
        items = list(node.items())
        for idx, (name, child) in enumerate(items):
            is_last = idx == len(items) - 1
            branch = "└── " if is_last else "├── "
            lines.append(prefix + branch + name)
            if isinstance(child, dict) and child:
                extension = "    " if is_last else "│   "
                _walk(child, prefix + extension)

    _walk(tree)
    return "\n".join(lines)


def render_tech_stack_from_manifest(
    entries: List[FileEntry],
    stack_prose: str = "",
) -> str:
    """Build tech_stack.md view: stack prose + rendered file tree."""
    prose = (stack_prose or "").strip()
    if not prose:
        prose = "# Technology Stack\n\n(See stack selection above.)"
    tree = render_file_tree_from_manifest(entries)
    if "## File Structure" in prose:
        head, _, _tail = prose.partition("## File Structure")
        prose = head.rstrip()
    return f"{prose}\n\n## File Structure\n\n```\n{tree}\n```\n"


def parse_supplementary_file_entries(text: str) -> List[FileEntry]:
    """Parse Pass 2b / seeder JSON array ``[{path, description}]``."""
    if not text or not text.strip():
        return []
    import re as _re

    candidates: List[str] = []
    tag_match = _re.search(
        r"<supplementary_files>\s*(.*?)\s*</supplementary_files>",
        text,
        _re.DOTALL | _re.IGNORECASE,
    )
    if tag_match:
        candidates.append(tag_match.group(1).strip())
    fence = _re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence:
        candidates.append(fence.group(1).strip())
    array_match = _re.search(r"(\[\s*\{[\s\S]*?\}\s*\])", text)
    if array_match:
        candidates.append(array_match.group(1).strip())

    for blob in candidates:
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list):
            continue
        entries: List[FileEntry] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            p = _normalize_manifest_path(str(item.get("path") or ""))
            if not p:
                continue
            entries.append({
                "path": p,
                "description": str(item.get("description") or f"Supplementary: {p}"),
                "manifest_source": "supplementary",
                "tier": "supplementary",
            })
        if entries:
            return entries
    return []


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
    # 1. Prefer package-manager identity on disk (any language)
    manifest_mod, _lang = read_package_manifest_identity(workspace)
    if manifest_mod and not _is_weak_module_name(manifest_mod):
        return manifest_mod

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
            (r'\bmodule\s+([a-zA-Z0-9_./@-]+)\b', lambda m: m.group(1)),
            (r'"name"\s*:\s*"([a-zA-Z0-9@/_-]+)"', lambda m: m.group(1)),
            (r'^\s*name\s*=\s*["\']([a-zA-Z0-9_./@-]+)["\']', lambda m: m.group(1)),
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


def package_has_domain_keywords(package_name: str, owns: Optional[List[str]] = None) -> bool:
    """True when a package looks like an HTTP boundary *or* domain/lifecycle owner."""
    tokens = normalize_workspace_path(package_name).lower().replace("/", " ").replace("_", " ").split()
    text = " ".join(tokens)
    if owns:
        text += " " + " ".join(o.lower() for o in owns)
    return any(kw in text for kw in _DOMAIN_KEYWORDS)


def missing_declared_source_files(contract: dict, workspace: Path) -> List[str]:
    """Return contract-declared source files that are absent on disk.

    Only checks known source suffixes (``.go``, ``.py``, …). Docs/config paths
    in ``packages[*].files`` are ignored.

    For Frappe scaffolding, flat ``{app}/hooks.py`` satisfies a declared nested
    ``{app}/{app}/hooks.py`` (and the reverse) so layout variants do not
    false-fail wiring reconciliation.
    """
    if not contract:
        return []
    workspace = Path(workspace)
    missing: List[str] = []
    for pkg_data in (contract.get("packages") or {}).values():
        if not isinstance(pkg_data, dict):
            continue
        for f in pkg_data.get("files") or []:
            if not isinstance(f, str) or not f.strip():
                continue
            rel = normalize_workspace_path(f)
            if not _has_source_suffix(rel):
                continue
            if (workspace / rel).is_file():
                continue
            alt = _frappe_scaffold_alt_path(rel)
            if alt and (workspace / alt).is_file():
                continue
            missing.append(rel)
    return missing


def _frappe_scaffold_alt_path(rel: str) -> Optional[str]:
    """Map flat↔nested Frappe hooks/modules/__init__ paths, else None."""
    parts = rel.replace("\\", "/").split("/")
    if len(parts) < 2:
        return None
    name = parts[-1]
    if name not in ("hooks.py", "modules.txt", "__init__.py"):
        return None
    # nested: app/app/hooks.py -> flat app/hooks.py
    if len(parts) >= 3 and parts[0] == parts[1]:
        return "/".join([parts[0], *parts[2:]])
    # flat: app/hooks.py -> nested app/app/hooks.py
    if len(parts) == 2:
        return f"{parts[0]}/{parts[0]}/{name}"
    return None


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

    for missing_path in missing_declared_source_files(contract, workspace):
        issues.append({
            "file": missing_path,
            "description": (
                f"declared source file missing: '{missing_path}' is in "
                f"wiring_contract.packages but not on disk"
            ),
            "type": "wiring_reconciliation",
            "severity": "error",
        })

    for pkg_dir in collect_unauthorized_sibling_packages(workspace, declared_prefixes):
        issues.append({
            "file": pkg_dir,
            "description": (
                f"unauthorized package: '{pkg_dir}' found on disk but not in contract.packages"
            ),
            "type": "wiring_reconciliation",
            "severity": "error",
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
                        "severity": "error",
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

    Always (relaxed or strict): flag competing domain/boundary packages under the
    same parent (e.g. ``internal/sandbox`` when ``internal/service`` is approved).

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
        "tests_integration/", "tests_e2e/", "test-fixtures/",
        "configuration/", "build/", "prerequisites/", "features/", "testing/",
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

    # Competing domain/boundary packages: sibling dirs with similar concern but not in contract
    domain_packages = [
        pkg_name
        for pkg_name, pkg_data in packages.items()
        if package_has_domain_keywords(pkg_name, pkg_data.get("owns") or [])
    ]

    if domain_packages:
        for path in declared_paths:
            if is_always_ok(path):
                continue
            candidate = unauthorized_package_prefix_for_path(path, declared_prefixes)
            if not candidate or candidate in packages:
                continue
            if package_has_domain_keywords(candidate, []):
                cand_parent = str(Path(normalize_workspace_path(candidate)).parent).replace("\\", "/")
                same_parent = [
                    p for p in domain_packages
                    if str(Path(normalize_workspace_path(p)).parent).replace("\\", "/") == cand_parent
                ]
                approved = same_parent[0] if same_parent else domain_packages[0]
                return (
                    f"Competing package '{candidate}' was introduced in tech stack, "
                    f"but '{approved}' is already the approved package for that concern. "
                    f"Do not add a parallel domain/API tree; put files under the approved packages only."
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
    stack_manifest = None
    if workspace:
        mp = Path(workspace) / "stack_manifest.json"
        if mp.is_file():
            try:
                stack_manifest = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                stack_manifest = None

    def _finalize(contract: dict | None) -> dict:
        if not contract:
            return contract  # type: ignore[return-value]
        fixed = normalize_frappe_wiring_contract(
            contract,
            solution_spec=solution_spec or "",
            design_spec=design_spec or "",
            tech_stack=tech_stack or "",
            stack_manifest=stack_manifest if isinstance(stack_manifest, dict) else None,
        )
        return ensure_package_file_paths(normalize_symbol_keys(fixed or contract)) or (fixed or contract)

    emitted = parse_emitted_wiring_contract(solution_spec or "", design_spec or "", tech_stack or "")
    if emitted:
        if language_hint and not emitted.get("language"):
            emitted["language"] = language_hint
        return _finalize(emitted)

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
            return _finalize(patched)
        logger.warning("wiring jq patch invalid; strengthening path-only seed from prose")

    strengthened = strengthen_contract_from_specs(
        seed,
        design_spec or "",
        solution_spec or "",
        tech_stack or "",
    )
    return _finalize(strengthened)
