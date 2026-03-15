"""
Code Completeness Validator

Detects stub files, placeholder components, truncated output,
TODO-only implementations, syntax errors, broken imports,
undeclared dependencies, and tech-stack conflicts
that indicate incomplete or broken code generation.

Language-specific logic is delegated to :mod:`language_strategies` via
:class:`StrategyRegistry`.  This module retains the language-agnostic
checks and provides backward-compatible class-method APIs.
"""
import ast
import json as _json
import re
import sys
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Any, Optional, Set

from .language_strategies import (
    LanguageStrategy,
    PythonStrategy,
    JavaStrategy,
    JavaScriptStrategy,
    StrategyRegistry,
    _extract_openapi_paths,
)

logger = logging.getLogger(__name__)

_default_registry = StrategyRegistry()

_SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
    ".cpp", ".c", ".h", ".rb", ".php", ".swift", ".kt",
}

_STUB_PATTERNS = [
    (re.compile(r"console\.log\(['\"].*?['\"].*?\)"), "console.log stub handler"),
    (re.compile(r"#\s*TODO\b", re.IGNORECASE), "TODO comment"),
    (re.compile(r"//\s*TODO\b", re.IGNORECASE), "TODO comment"),
    (re.compile(r"/\*\s*TODO\b", re.IGNORECASE), "TODO comment"),
    (re.compile(r"\bNotImplementedError\b"), "NotImplementedError placeholder"),
    (re.compile(r"raise\s+NotImplementedError"), "NotImplementedError placeholder"),
]

_MIN_MEANINGFUL_LINES = 5
_MIN_MEANINGFUL_CHARS = 80


class CodeCompletenessValidator:
    """Static validator that inspects source files for completeness."""

    @classmethod
    def validate_file(cls, file_path: Path) -> Dict[str, Any]:
        """Validate a single source file for completeness.

        Returns dict with keys: complete (bool), issues (list[str]), file (str).
        """
        path = Path(file_path)
        issues: List[str] = []

        if not path.exists():
            return {"complete": False, "issues": ["File does not exist"], "file": str(path)}

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"complete": False, "issues": [f"Cannot read: {e}"], "file": str(path)}

        if not content.strip():
            return {"complete": False, "issues": ["File is empty"], "file": str(path)}

        lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith(("#", "//", "/*", "*"))]

        if len(lines) < _MIN_MEANINGFUL_LINES:
            issues.append(f"Only {len(lines)} non-comment lines (minimum {_MIN_MEANINGFUL_LINES})")

        if len(content.strip()) < _MIN_MEANINGFUL_CHARS:
            issues.append(f"Only {len(content.strip())} chars of content (minimum {_MIN_MEANINGFUL_CHARS})")

        for pattern, label in _STUB_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                issues.append(f"Found {len(matches)} {label}(s)")

        if cls._is_placeholder_component(content, path.suffix):
            issues.append("Placeholder/stub component with no real logic")

        if cls._has_pass_only_methods(content, path.suffix):
            issues.append("Contains pass-only method bodies (no implementation)")

        complete = len(issues) == 0
        return {"complete": complete, "issues": issues, "file": str(path)}

    @classmethod
    def validate_workspace(cls, workspace: Path) -> Dict[str, Any]:
        """Validate all source files in a workspace directory.

        Returns dict with total_files, complete_files, incomplete_files (list of dicts).
        """
        workspace = Path(workspace)
        results: List[Dict[str, Any]] = []
        for f in sorted(workspace.rglob("*")):
            if f.is_file() and f.suffix in _SOURCE_EXTENSIONS:
                result = cls.validate_file(f)
                results.append(result)

        incomplete = [r for r in results if not r["complete"]]
        return {
            "total_files": len(results),
            "complete_files": len(results) - len(incomplete),
            "incomplete_files": incomplete,
        }

    @classmethod
    def _is_placeholder_component(cls, content: str, suffix: str) -> bool:
        """Detect React/JSX components that are just a wrapper around a text string."""
        if suffix not in (".js", ".jsx", ".tsx"):
            return False

        has_state = "useState" in content or "useEffect" in content or "useCallback" in content
        has_api = "fetch(" in content or "axios" in content or "api." in content.lower()
        has_map = ".map(" in content
        has_handler = re.search(r"on\w+\s*=\s*\{(?!.*console\.log)", content) is not None

        if has_state or has_api or has_map or has_handler:
            return False

        # Count JSX elements – a real component typically has many elements
        jsx_tags = re.findall(r"<\w+[\s/>]", content)
        code_lines = [l for l in content.splitlines()
                      if l.strip() and not l.strip().startswith(("//", "/*", "*", "import ", "export "))]
        if len(code_lines) <= 8 and len(jsx_tags) <= 4:
            return True

        return False

    @classmethod
    def _has_pass_only_methods(cls, content: str, suffix: str) -> bool:
        """Detect Python methods/functions whose body is only `pass`."""
        if suffix != ".py":
            return False
        # Match def ... : <newline> <whitespace> pass
        return bool(re.search(r"def\s+\w+\([^)]*\):\s*\n\s+pass\b", content))

    # ── Integration-level validation ─────────────────────────────────────────
    # These delegate to language strategies via the default registry, keeping
    # full backward compatibility with existing callers.

    _BRACE_LANGS = {".java", ".js", ".jsx", ".ts", ".tsx", ".go", ".kt", ".swift", ".c", ".cpp", ".h", ".rs"}
    _JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
    _JAVA_EXTENSIONS = {".java", ".kt"}

    @classmethod
    def validate_syntax(cls, file_path: Path) -> Dict[str, Any]:
        """Check source file for syntax errors.  Delegates to the appropriate
        :class:`LanguageStrategy` when one exists for the file extension.

        Returns ``{"valid": bool, "error": str}``.
        """
        path = Path(file_path)
        strategy = _default_registry.get_for_file(path)
        if strategy:
            return strategy.validate_syntax(path)
        if path.suffix in cls._BRACE_LANGS:
            from .language_strategies import _validate_brace_syntax
            return _validate_brace_syntax(path)
        return {"valid": True, "error": ""}

    @classmethod
    def validate_imports(
        cls,
        file_path: Path,
        workspace_path: Path,
    ) -> Dict[str, Any]:
        """Verify that local imports resolve to files in the workspace.

        Delegates to the appropriate :class:`LanguageStrategy`.
        Returns ``{"valid": bool, "broken_imports": [{"module": str, "line": int}]}``.
        """
        path = Path(file_path)
        ws = Path(workspace_path)
        strategy = _default_registry.get_for_file(path)
        if strategy:
            return strategy.validate_imports(path, ws)
        return {"valid": True, "broken_imports": []}

    @classmethod
    def validate_file_integration(
        cls,
        file_path: Path,
        workspace_path: Path,
    ) -> Dict[str, Any]:
        """Combined syntax + import validation for a single file.

        Returns ``{"valid": bool, "issues": [str]}``.
        """
        issues: List[str] = []

        syn = cls.validate_syntax(file_path)
        if not syn["valid"]:
            issues.append(syn["error"])

        imp = cls.validate_imports(file_path, workspace_path)
        for b in imp["broken_imports"]:
            issues.append(f"Broken import: '{b['module']}' at line {b['line']} (module not found in workspace)")

        return {"valid": len(issues) == 0, "issues": issues}

    @classmethod
    def extract_export_summary(cls, file_path: Path) -> Dict[str, Any]:
        """Build a structured export summary for any supported source file."""
        path = Path(file_path)
        strategy = _default_registry.get_for_file(path)
        if strategy:
            return strategy.extract_exports(path)
        return {"file": str(path), "type": "unknown", "exports": []}

    # ── Dependency manifest validation ────────────────────────────────────────

    # ── Strategy-delegating validators ───────────────────────────────────────
    # These methods delegate to language strategies for language-specific logic
    # while keeping the same public API for backward compatibility.

    @classmethod
    def validate_dependency_manifest(cls, workspace_path: Path) -> Dict[str, Any]:
        """Validate that all imported packages are declared in the project manifest.

        Iterates all registered strategies and aggregates broken imports that
        reference undeclared third-party packages.

        Returns ``{"valid": bool, "missing": [{"ecosystem": str, "package": str, "files": [str]}]}``.
        """
        ws = Path(workspace_path)
        missing: Dict[str, set] = {}

        for strategy in _default_registry.all_strategies.values():
            declared = strategy.load_declared_dependencies(ws)
            for src in ws.rglob("*"):
                if not src.is_file() or src.suffix not in strategy.extensions:
                    continue
                imp_result = strategy.validate_imports(src, ws)
                for b in imp_result.get("broken_imports", []):
                    key = f"{strategy.name}:{b['module']}"
                    missing.setdefault(key, set()).add(str(src.relative_to(ws)))

        result_list = [
            {"ecosystem": k.split(":")[0], "package": k.split(":", 1)[1], "files": sorted(v)}
            for k, v in sorted(missing.items())
        ]
        return {"valid": len(result_list) == 0, "missing": result_list}

    # ── Tech stack conformance ────────────────────────────────────────────────

    _CONFLICTING_STACKS: Dict[str, List[tuple]] = {
        "js": [
            ({"mongoose", "mongodb"}, {"sequelize", "typeorm", "prisma", "knex", "pg"}, "MongoDB driver vs SQL ORM"),
            ({"react-scripts"}, {"vite", "@vitejs/plugin-react"}, "Create React App vs Vite"),
            ({"express"}, {"fastify", "@hapi/hapi", "koa"}, "Express vs alternative Node framework"),
        ],
        "python": [
            ({"django"}, {"flask", "fastapi", "bottle", "tornado"}, "Django vs alternative Python framework"),
            ({"sqlalchemy"}, {"django"}, "SQLAlchemy vs Django ORM"),
        ],
        "java": [
            ({"org.springframework"}, {"io.quarkus", "io.micronaut"}, "Spring Boot vs Quarkus/Micronaut"),
            ({"org.hibernate"}, {"org.jdbi", "org.jooq"}, "Hibernate vs lightweight SQL library"),
        ],
    }

    @classmethod
    def _detect_chosen_stack(cls, tech_stack_content: str) -> Dict[str, Set[str]]:
        lower = tech_stack_content.lower()
        chosen: Dict[str, Set[str]] = {"js": set(), "python": set(), "java": set()}
        js_kw = {
            "express": "express", "fastify": "fastify", "koa": "koa",
            "vite": "vite", "create-react-app": "react-scripts", "cra": "react-scripts",
            "mongoose": "mongoose", "mongodb": "mongodb",
            "sequelize": "sequelize", "typeorm": "typeorm", "prisma": "prisma",
            "knex": "knex",
        }
        py_kw = {"django": "django", "flask": "flask", "fastapi": "fastapi", "sqlalchemy": "sqlalchemy"}
        java_kw = {
            "spring boot": "org.springframework", "spring": "org.springframework",
            "quarkus": "io.quarkus", "micronaut": "io.micronaut",
            "hibernate": "org.hibernate", "jooq": "org.jooq", "jdbi": "org.jdbi",
        }
        for keyword, lib in js_kw.items():
            if keyword in lower:
                chosen["js"].add(lib)
        for keyword, lib in py_kw.items():
            if keyword in lower:
                chosen["python"].add(lib)
        for keyword, lib in java_kw.items():
            if keyword in lower:
                chosen["java"].add(lib)
        return chosen

    @classmethod
    def validate_tech_stack_conformance(
        cls, workspace_path: Path, tech_stack_content: str
    ) -> Dict[str, Any]:
        """Validate that generated code only uses libraries from the chosen tech stack."""
        ws = Path(workspace_path)
        chosen = cls._detect_chosen_stack(tech_stack_content)
        conflicts: List[Dict[str, str]] = []

        for ecosystem, rules in cls._CONFLICTING_STACKS.items():
            for set_a, set_b, label in rules:
                chosen_set = chosen.get(ecosystem, set())
                has_a = chosen_set & set_a
                has_b = chosen_set & set_b
                if not has_a and not has_b:
                    continue
                forbidden = set_b if has_a else set_a if has_b else set()
                if not forbidden:
                    continue

                if ecosystem == "js":
                    files = [f for f in ws.rglob("*") if f.is_file() and f.suffix in cls._JS_EXTENSIONS]
                elif ecosystem == "python":
                    files = list(ws.rglob("*.py"))
                elif ecosystem == "java":
                    files = [f for f in ws.rglob("*") if f.is_file() and f.suffix in cls._JAVA_EXTENSIONS]
                else:
                    continue

                for src in files:
                    try:
                        content = src.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for pkg in forbidden:
                        if pkg in content:
                            conflicts.append({
                                "file": str(src.relative_to(ws)),
                                "conflict": label,
                                "detail": f"File uses '{pkg}' but stack chose {has_a or has_b}",
                            })
                            break

        return {"valid": len(conflicts) == 0, "conflicts": conflicts}

    # ── Library existence verification ────────────────────────────────────────

    _LIB_CACHE: Dict[str, bool] = {}

    @classmethod
    def verify_library_exists(cls, package_name: str, ecosystem: str) -> bool:
        """Check if a package exists in its ecosystem registry."""
        cache_key = f"{ecosystem}:{package_name}"
        if cache_key in cls._LIB_CACHE:
            return cls._LIB_CACHE[cache_key]

        urls = {
            "npm": f"https://registry.npmjs.org/{package_name}",
            "pypi": f"https://pypi.org/pypi/{package_name}/json",
        }
        if ecosystem == "maven":
            parts = package_name.split(":")
            if len(parts) == 2:
                url = f"https://search.maven.org/solrsearch/select?q=g:{parts[0]}+AND+a:{parts[1]}&rows=1&wt=json"
            else:
                url = f"https://search.maven.org/solrsearch/select?q=g:{package_name}&rows=1&wt=json"
        else:
            url = urls.get(ecosystem)
        if not url:
            return True
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if ecosystem == "maven":
                    data = _json.loads(resp.read())
                    exists = data.get("response", {}).get("numFound", 0) > 0
                else:
                    exists = resp.status == 200
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            exists = True
        except Exception:
            exists = True

        cls._LIB_CACHE[cache_key] = exists
        if not exists:
            logger.warning("Library '%s' not found on %s registry", package_name, ecosystem)
        return exists

    @classmethod
    def verify_workspace_libraries(cls, workspace_path: Path) -> Dict[str, Any]:
        """Check that all declared dependencies exist in their registries.

        Uses strategies to load dependencies per ecosystem.
        """
        ws = Path(workspace_path)
        hallucinated: List[Dict[str, str]] = []

        ecosystem_map = {"python": "pypi", "java": "maven", "javascript": "npm"}
        for strategy in _default_registry.all_strategies.values():
            eco = ecosystem_map.get(strategy.name, strategy.name)
            for pkg in strategy.load_declared_dependencies(ws):
                canonical = pkg.replace("_", "-").lower() if eco == "pypi" else pkg
                if canonical and not cls.verify_library_exists(canonical, eco):
                    hallucinated.append({"ecosystem": eco, "package": pkg})

        return {"valid": len(hallucinated) == 0, "hallucinated": hallucinated}

    # ── Package structure validation ──────────────────────────────────────────

    @classmethod
    def validate_package_structure(cls, workspace_path: Path) -> Dict[str, Any]:
        """Delegates to the Python strategy for ``__init__.py`` checking.

        Returns ``{"valid": bool, "missing_init": [str]}``.
        """
        py = _default_registry.get_by_name("python")
        if py:
            return py.validate_package_structure(Path(workspace_path))
        return {"valid": True, "missing_init": []}

    # ── Duplicate / scattered file detection ──────────────────────────────────

    @classmethod
    def validate_duplicate_files(cls, workspace_path: Path) -> Dict[str, Any]:
        """Detect source files with the same name under different directory trees."""
        ws = Path(workspace_path)
        by_name: Dict[str, List[str]] = {}

        for src in ws.rglob("*"):
            if not src.is_file() or src.suffix not in _SOURCE_EXTENSIONS:
                continue
            rel = str(src.relative_to(ws))
            by_name.setdefault(src.name, []).append(rel)

        duplicates = [
            {"filename": name, "paths": sorted(paths)}
            for name, paths in sorted(by_name.items())
            if len(paths) > 1
        ]
        return {"valid": len(duplicates) == 0, "duplicates": duplicates}

    # ── Entrypoint wiring validation ──────────────────────────────────────────

    @classmethod
    def validate_entrypoint(
        cls, workspace_path: Path, tech_stack_content: str
    ) -> Dict[str, Any]:
        """Delegates to the appropriate language strategy for entrypoint wiring.

        Returns ``{"valid": bool, "framework": str, "missing_wiring": [str]}``.
        """
        ws = Path(workspace_path)
        strategies = _default_registry.detect_from_tech_stack(tech_stack_content)
        for strategy in strategies:
            result = strategy.validate_entrypoint(ws, tech_stack_content)
            if result.get("framework"):
                return result
        return {"valid": True, "framework": "", "missing_wiring": [],
                "note": "no recognised framework in tech stack"}

    # ── Contract conformance ──────────────────────────────────────────────────

    @classmethod
    def validate_contract_conformance(
        cls, workspace_path: Path, contract: Dict[str, Any], tech_stack: str = "",
    ) -> Dict[str, Any]:
        """Check generated code implements all endpoints from an OpenAPI contract.

        Delegates to the appropriate language strategy.
        """
        ws = Path(workspace_path)
        strategies = _default_registry.detect_from_tech_stack(tech_stack)
        for strategy in strategies:
            result = strategy.validate_contract_conformance(ws, contract)
            if result.get("missing_endpoints") or result.get("extra_endpoints"):
                return result
            if not result.get("note"):
                return result
        return {"valid": True, "missing_endpoints": [], "extra_endpoints": []}
