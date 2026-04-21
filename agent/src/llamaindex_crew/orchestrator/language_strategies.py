"""
Language Strategy Framework

Provides a pluggable, YAML-configurable strategy pattern for language-specific
validation.  Each ``LanguageStrategy`` encapsulates syntax checking, import
resolution, package-structure validation, entrypoint wiring detection, export
extraction, dependency loading, and API-contract conformance for a single
language ecosystem.

A ``StrategyRegistry`` auto-detects the project language from the tech stack
or file extensions and returns the appropriate strategy.  New languages are
added by dropping a YAML config file into the strategies directory.
"""
from __future__ import annotations

import ast
import json as _json
import logging
import re
import sys
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Abstract base
# ═══════════════════════════════════════════════════════════════════════════════

class LanguageStrategy(ABC):
    """Encapsulates all language-specific validation for one ecosystem."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. ``'python'``, ``'java'``, ``'javascript'``."""

    @property
    @abstractmethod
    def extensions(self) -> Set[str]:
        """File extensions handled by this strategy (including the dot)."""

    # ── per-file checks ───────────────────────────────────────────────────

    @abstractmethod
    def validate_syntax(self, file_path: Path) -> Dict[str, Any]:
        """Return ``{"valid": bool, "error": str}``."""

    @abstractmethod
    def validate_imports(
        self, file_path: Path, workspace: Path,
    ) -> Dict[str, Any]:
        """Return ``{"valid": bool, "broken_imports": [{"module": str, "line": int}]}``."""

    @abstractmethod
    def extract_exports(self, file_path: Path) -> Dict[str, Any]:
        """Return a structured export summary for a single file."""

    # ── workspace-level checks ────────────────────────────────────────────

    @abstractmethod
    def validate_package_structure(self, workspace: Path) -> Dict[str, Any]:
        """Return ``{"valid": bool, ...}`` with language-specific detail keys."""

    @abstractmethod
    def validate_entrypoint(
        self, workspace: Path, tech_stack: str,
    ) -> Dict[str, Any]:
        """Return ``{"valid": bool, "framework": str, "missing_wiring": [str]}``."""

    @abstractmethod
    def load_declared_dependencies(self, workspace: Path) -> Set[str]:
        """Return the set of declared third-party package names."""

    @abstractmethod
    def validate_contract_conformance(
        self, workspace: Path, contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check generated code implements all endpoints listed in an OpenAPI contract.

        Return ``{"valid": bool, "missing_endpoints": [str], "extra_endpoints": [str]}``.
        """

    # ── optional: configurable wiring from YAML ───────────────────────────

    def configure_from_yaml(self, config: Dict[str, Any]) -> None:
        """Override framework wiring / entrypoint config from a YAML dict.

        Called by :class:`StrategyRegistry` when loading strategies from config.
        The default implementation is a no-op; subclasses override as needed.
        """


# ═══════════════════════════════════════════════════════════════════════════════
# Shared helpers (used by multiple strategies)
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_strings_and_comments(source: str) -> str:
    """Remove string literals and comments so delimiter counting isn't fooled."""
    result = re.sub(r'//[^\n]*', '', source)
    result = re.sub(r'/\*.*?\*/', '', result, flags=re.DOTALL)
    result = re.sub(r'"(?:[^"\\]|\\.)*"', '""', result)
    result = re.sub(r"'(?:[^'\\]|\\.)*'", "''", result)
    result = re.sub(r'`(?:[^`\\]|\\.)*`', '``', result)
    return result


def _validate_brace_syntax(path: Path) -> Dict[str, Any]:
    """Check that braces, brackets, and parens are balanced."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"valid": False, "error": str(e)}

    cleaned = _strip_strings_and_comments(source)
    pairs = {"{": "}", "[": "]", "(": ")"}
    closing = set(pairs.values())
    stack: List[tuple] = []

    for i, ch in enumerate(cleaned):
        if ch in pairs:
            stack.append((ch, i))
        elif ch in closing:
            if not stack:
                line = source[:i].count("\n") + 1
                return {"valid": False, "error": f"Unmatched closing '{ch}' at line {line}"}
            opener, _ = stack.pop()
            if pairs[opener] != ch:
                line = source[:i].count("\n") + 1
                return {
                    "valid": False,
                    "error": f"Mismatched brace: expected '{pairs[opener]}' but got '{ch}' at line {line}",
                }

    if stack:
        opener, pos = stack[-1]
        line = source[:pos].count("\n") + 1
        return {"valid": False, "error": f"Unmatched opening '{opener}' at line {line} (no matching closing brace)"}

    return {"valid": True, "error": ""}


def _extract_openapi_paths(contract: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Parse an OpenAPI contract dict and return ``{path: {methods}}``."""
    paths: Dict[str, Set[str]] = {}
    for path_str, methods in (contract.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        http_methods = {
            m.upper()
            for m in methods
            if m.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
        }
        if http_methods:
            paths[path_str] = http_methods
    return paths


# ═══════════════════════════════════════════════════════════════════════════════
# Python strategy
# ═══════════════════════════════════════════════════════════════════════════════

class PythonStrategy(LanguageStrategy):

    @property
    def name(self) -> str:
        return "python"

    @property
    def extensions(self) -> Set[str]:
        return {".py"}

    # -- configurable framework wiring --
    _FRAMEWORK_WIRING: Dict[str, List[Dict[str, Any]]] = {
        "flask": [
            {"pattern": re.compile(r"Flask\s*\(\s*__name__\s*\)"), "label": "Flask app creation"},
            {"pattern": re.compile(r"\.init_app\s*\(|db\s*=\s*SQLAlchemy\s*\(\s*app\s*\)"), "label": "db.init_app() or SQLAlchemy(app)"},
            {"pattern": re.compile(r"@app\.route\s*\(|import\s+routes|from\s+\S*routes\S*\s+import"), "label": "route registration or import"},
        ],
        "fastapi": [
            {"pattern": re.compile(r"FastAPI\s*\("), "label": "FastAPI app creation"},
            {"pattern": re.compile(r"\.include_router\s*\(|@app\.(get|post|put|delete|patch)\s*\("), "label": "router or route registration"},
        ],
        "django": [
            {"pattern": re.compile(r"INSTALLED_APPS\s*="), "label": "settings INSTALLED_APPS"},
            {"pattern": re.compile(r"urlpatterns\s*="), "label": "urlpatterns"},
        ],
    }

    _ENTRYPOINT_FILENAMES: Dict[str, Set[str]] = {
        "flask": {"app.py", "main.py", "server.py", "wsgi.py", "__init__.py"},
        "fastapi": {"main.py", "app.py", "server.py"},
        "django": {"settings.py", "urls.py", "manage.py"},
    }

    _ROUTE_PATTERNS: Dict[str, re.Pattern] = {
        "flask": re.compile(r"""@\w+\.route\s*\(\s*['"]([^'"]+)['"]"""),
        "fastapi": re.compile(r"""@\w+\.(get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)['"]"""),
        "django": re.compile(r"""path\s*\(\s*['"]([^'"]+)['"]"""),
    }

    # -- syntax --

    def validate_syntax(self, file_path: Path) -> Dict[str, Any]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=str(file_path))
            return {"valid": True, "error": ""}
        except SyntaxError as e:
            return {"valid": False, "error": f"SyntaxError at line {e.lineno}: {e.msg}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    # -- imports --

    def validate_imports(self, file_path: Path, workspace: Path) -> Dict[str, Any]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return {"valid": True, "broken_imports": []}

        third_party = self._load_third_party_names(workspace)
        stdlib = self._stdlib_names()
        broken: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in stdlib and top not in third_party:
                        if not self._module_exists(alias.name, workspace):
                            broken.append({"module": alias.name, "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top not in stdlib and top not in third_party:
                        if not self._module_exists(node.module, workspace):
                            broken.append({"module": node.module, "line": node.lineno})

        return {"valid": len(broken) == 0, "broken_imports": broken}

    # -- exports --

    def extract_exports(self, file_path: Path) -> Dict[str, Any]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            return {"file": str(file_path), "type": "python", "exports": []}

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            names = [
                                elt.value
                                for elt in node.value.elts
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            ]
                            return {"file": str(file_path), "type": "python", "exports": names}

        exports: List[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    exports.append(node.name)
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    exports.append(node.name)
        return {"file": str(file_path), "type": "python", "exports": exports}

    # -- package structure --

    def validate_package_structure(self, workspace: Path) -> Dict[str, Any]:
        missing: Set[str] = set()
        for src in workspace.rglob("*.py"):
            if not src.is_file():
                continue
            try:
                tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            for node in ast.walk(tree):
                pkg_parts: List[str] = []
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        pkg_parts = alias.name.split(".")[:-1]
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        pkg_parts = node.module.split(".")[:-1]
                for i in range(len(pkg_parts)):
                    d = workspace / "/".join(pkg_parts[: i + 1])
                    if d.is_dir() and not (d / "__init__.py").exists():
                        missing.add(str(d.relative_to(workspace)))
        return {"valid": len(missing) == 0, "missing_init": sorted(missing)}

    # -- entrypoint wiring --

    def validate_entrypoint(self, workspace: Path, tech_stack: str) -> Dict[str, Any]:
        lower = tech_stack.lower()
        framework = ""
        for fw in ("flask", "fastapi", "django"):
            if fw in lower:
                framework = fw
                break
        if not framework:
            return {"valid": True, "framework": "", "missing_wiring": [],
                    "note": "no recognised Python framework in tech stack"}

        expected = self._ENTRYPOINT_FILENAMES.get(framework, set())
        rules = self._FRAMEWORK_WIRING.get(framework, [])

        combined = ""
        for src in workspace.rglob("*"):
            if src.is_file() and src.name in expected:
                try:
                    combined += src.read_text(encoding="utf-8", errors="replace") + "\n"
                except Exception:
                    pass

        if not combined:
            return {
                "valid": False, "framework": framework,
                "missing_wiring": [f"No entrypoint file found (expected one of: {', '.join(sorted(expected))})"],
            }
        missing = [r["label"] for r in rules if not r["pattern"].search(combined)]
        return {"valid": len(missing) == 0, "framework": framework, "missing_wiring": missing}

    # -- dependencies --

    def load_declared_dependencies(self, workspace: Path) -> Set[str]:
        names: Set[str] = set()
        names.update(self._load_third_party_names(workspace))
        pyproject = workspace / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'["\']([a-zA-Z0-9_-]+)(?:\[.*?\])?(?:[><=!~].*?)?["\']', content):
                    pkg = m.group(1)
                    names.add(pkg)
                    names.add(pkg.replace("-", "_").lower())
                    names.add(pkg.replace("-", "_"))
            except Exception:
                pass
        return names

    # -- contract conformance --

    def validate_contract_conformance(
        self, workspace: Path, contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected = _extract_openapi_paths(contract)
        if not expected:
            return {"valid": True, "missing_endpoints": [], "extra_endpoints": []}

        lower_stack = ""
        for fw in ("flask", "fastapi", "django"):
            pattern = self._ROUTE_PATTERNS.get(fw)
            if not pattern:
                continue
            found_any = False
            for src in workspace.rglob("*.py"):
                try:
                    content = src.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if pattern.search(content):
                    found_any = True
                    lower_stack = fw
                    break
            if found_any:
                break

        if not lower_stack:
            return {"valid": True, "missing_endpoints": [], "extra_endpoints": [],
                    "note": "could not detect Python web framework from source"}

        route_pattern = self._ROUTE_PATTERNS[lower_stack]
        found_routes: Set[str] = set()
        for src in workspace.rglob("*.py"):
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in route_pattern.finditer(content):
                if lower_stack == "fastapi":
                    found_routes.add(m.group(2))
                else:
                    found_routes.add(m.group(1))

        normalised_found = {self._normalise_route(r) for r in found_routes}
        normalised_expected = {self._normalise_route(r) for r in expected}

        missing = sorted(normalised_expected - normalised_found)
        extra = sorted(normalised_found - normalised_expected)
        return {"valid": len(missing) == 0, "missing_endpoints": missing, "extra_endpoints": extra}

    # -- YAML configuration --

    def configure_from_yaml(self, config: Dict[str, Any]) -> None:
        entrypoint_cfg = config.get("checks", {}).get("entrypoint", {})
        frameworks = entrypoint_cfg.get("frameworks", {})
        for fw_name, fw_cfg in frameworks.items():
            patterns = []
            for p in fw_cfg.get("patterns", []):
                patterns.append({"pattern": re.compile(p["regex"]), "label": p["label"]})
            if patterns:
                self._FRAMEWORK_WIRING[fw_name] = patterns
            files = fw_cfg.get("files")
            if files:
                self._ENTRYPOINT_FILENAMES[fw_name] = set(files)

    # -- internal helpers --

    @staticmethod
    def _normalise_route(route: str) -> str:
        """Normalise /todos/{id} and /todos/<int:id> to /todos/{id}."""
        normalised = re.sub(r"<[^>]*>", "{param}", route)
        normalised = re.sub(r"\{[^}]+\}", "{param}", normalised)
        return normalised.rstrip("/") or "/"

    @staticmethod
    def _stdlib_names() -> set:
        if hasattr(sys, "stdlib_module_names"):
            return sys.stdlib_module_names
        import pkgutil
        return {m.name for m in pkgutil.iter_modules() if m.module_finder is not None} | {
            "os", "sys", "re", "json", "pathlib", "datetime", "typing",
            "collections", "functools", "itertools", "math", "hashlib",
            "logging", "unittest", "tempfile", "shutil", "sqlite3",
            "abc", "io", "copy", "enum", "dataclasses", "contextlib",
            "asyncio", "threading", "subprocess", "uuid", "time",
        }

    _KNOWN_FRAMEWORKS = frozenset({
        "frappe", "erpnext", "frappe_microservice",
        "django", "flask", "fastapi", "celery", "gunicorn",
        "requests", "httpx", "pydantic", "sqlalchemy",
        "numpy", "pandas", "scipy", "matplotlib",
        "pytest", "click", "typer", "boto3", "redis",
    })

    @classmethod
    def _load_third_party_names(cls, workspace: Path) -> set:
        names: set = set(cls._KNOWN_FRAMEWORKS)
        for manifest in ("requirements.txt", "setup.py", "setup.cfg"):
            req_file = workspace / manifest
            if req_file.exists():
                for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pkg = re.split(r"[>=<!\[;]", line)[0].strip()
                        if pkg and pkg[0].isalpha():
                            names.add(pkg.replace("-", "_").lower())
                            names.add(pkg.replace("-", "_"))
                            names.add(pkg.replace("_", "-"))
                            names.add(pkg)
        return names

    @staticmethod
    def _module_exists(module_path: str, workspace: Path) -> bool:
        parts = module_path.split(".")
        for depth in range(len(parts), 0, -1):
            sub = parts[:depth]
            if len(sub) > 1:
                candidate = workspace / "/".join(sub[:-1]) / (sub[-1] + ".py")
            else:
                candidate = workspace / (sub[0] + ".py")
            if candidate.exists():
                if not PythonStrategy._intermediate_packages_valid(sub[:-1], workspace):
                    return False
                return True
            pkg_dir = workspace / "/".join(sub)
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                if not PythonStrategy._intermediate_packages_valid(sub[:-1], workspace):
                    return False
                return True
        return False

    @staticmethod
    def _intermediate_packages_valid(dir_parts: List[str], workspace: Path) -> bool:
        for i in range(len(dir_parts)):
            pkg_dir = workspace / "/".join(dir_parts[: i + 1])
            if pkg_dir.is_dir() and not (pkg_dir / "__init__.py").exists():
                return False
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# Java / Kotlin strategy
# ═══════════════════════════════════════════════════════════════════════════════

class JavaStrategy(LanguageStrategy):

    @property
    def name(self) -> str:
        return "java"

    @property
    def extensions(self) -> Set[str]:
        return {".java", ".kt"}

    _JAVA_STDLIB_PREFIXES = frozenset({
        "java.", "javax.", "jakarta.",
        "org.w3c.", "org.xml.", "org.ietf.",
        "sun.", "com.sun.", "jdk.",
    })

    _FRAMEWORK_WIRING: Dict[str, List[Dict[str, Any]]] = {
        "spring": [
            {"pattern": re.compile(r"@SpringBootApplication"), "label": "@SpringBootApplication"},
            {"pattern": re.compile(r"SpringApplication\.run\s*\("), "label": "SpringApplication.run()"},
        ],
        "quarkus": [
            {"pattern": re.compile(r"@(?:ApplicationScoped|Singleton|RequestScoped|QuarkusMain|jakarta\.ws\.rs)"), "label": "Quarkus CDI/JAX-RS annotation"},
        ],
    }

    _ENTRYPOINT_FILENAMES: Dict[str, Set[str]] = {
        "spring": {"Application.java", "App.java"},
        "quarkus": {"Application.java", "App.java", "TaskApplication.java", "Main.java"},
    }

    _ROUTE_PATTERNS: Dict[str, re.Pattern] = {
        "spring": re.compile(
            r"""@(?:Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?['"]([^'"]+)['"]"""
        ),
        "quarkus": re.compile(
            r"""@(?:GET|POST|PUT|PATCH|DELETE)\b|@Path\s*\(\s*['"]([^'"]+)['"]"""
        ),
    }

    def validate_syntax(self, file_path: Path) -> Dict[str, Any]:
        return _validate_brace_syntax(file_path)

    def validate_imports(self, file_path: Path, workspace: Path) -> Dict[str, Any]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"valid": True, "broken_imports": []}

        third_party = self._load_java_third_party_prefixes(workspace)
        broken: List[Dict[str, Any]] = []

        for lineno, line in enumerate(source.splitlines(), 1):
            m = re.match(r'^\s*import\s+(static\s+)?([a-zA-Z0-9_.]+)\s*;', line)
            if not m:
                continue
            module = m.group(2)
            if any(module.startswith(p) for p in self._JAVA_STDLIB_PREFIXES):
                continue
            if any(module.startswith(p) for p in third_party):
                continue
            if not self._java_import_exists(module, workspace):
                broken.append({"module": module, "line": lineno})

        return {"valid": len(broken) == 0, "broken_imports": broken}

    def extract_exports(self, file_path: Path) -> Dict[str, Any]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"file": str(file_path), "type": "java", "exports": []}
        types: List[str] = []
        for m in re.finditer(
            r"public\s+(?:abstract\s+|final\s+|static\s+)*(?:class|interface|enum|record)\s+(\w+)",
            source,
        ):
            types.append(m.group(1))
        return {"file": str(file_path), "type": "java", "exports": types}

    def validate_package_structure(self, workspace: Path) -> Dict[str, Any]:
        issues: List[str] = []
        for src in workspace.rglob("*.java"):
            if not src.is_file():
                continue
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            m = re.search(r"^\s*package\s+([\w.]+)\s*;", content, re.MULTILINE)
            if not m:
                continue
            expected_dir = m.group(1).replace(".", "/")
            rel = str(src.relative_to(workspace))
            if expected_dir not in rel:
                issues.append(f"{rel}: package '{m.group(1)}' does not match directory")
        return {"valid": len(issues) == 0, "issues": issues}

    def validate_entrypoint(self, workspace: Path, tech_stack: str) -> Dict[str, Any]:
        lower = tech_stack.lower()
        framework = ""
        for fw in ("quarkus", "spring"):
            if fw in lower:
                framework = fw
                break
        if not framework:
            return {"valid": True, "framework": "", "missing_wiring": [],
                    "note": "no recognised Java framework in tech stack"}

        expected = self._ENTRYPOINT_FILENAMES.get(framework, set())
        rules = self._FRAMEWORK_WIRING.get(framework, [])

        combined = ""
        for src in workspace.rglob("*"):
            if src.is_file() and src.name in expected:
                try:
                    combined += src.read_text(encoding="utf-8", errors="replace") + "\n"
                except Exception:
                    pass

        # For Quarkus, also scan all Java files for JAX-RS annotations if no
        # explicit entrypoint file was found (Quarkus apps often don't need one).
        if not combined and framework == "quarkus":
            for src in workspace.rglob("*.java"):
                if src.is_file():
                    try:
                        combined += src.read_text(encoding="utf-8", errors="replace") + "\n"
                    except Exception:
                        pass

        if not combined:
            return {
                "valid": False, "framework": framework,
                "missing_wiring": [f"No entrypoint file found (expected one of: {', '.join(sorted(expected))})"],
            }
        missing = [r["label"] for r in rules if not r["pattern"].search(combined)]
        return {"valid": len(missing) == 0, "framework": framework, "missing_wiring": missing}

    def load_declared_dependencies(self, workspace: Path) -> Set[str]:
        deps: Set[str] = set()
        pom = workspace / "pom.xml"
        if pom.exists():
            content = pom.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(
                r"<groupId>\s*([^<]+?)\s*</groupId>\s*<artifactId>\s*([^<]+?)\s*</artifactId>",
                content, re.DOTALL,
            ):
                deps.add(f"{m.group(1)}:{m.group(2)}")
        for gf in ("build.gradle", "build.gradle.kts"):
            gradle = workspace / gf
            if gradle.exists():
                content = gradle.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r"['\"]([a-zA-Z0-9_.]+):([a-zA-Z0-9_.-]+):", content):
                    deps.add(f"{m.group(1)}:{m.group(2)}")
        return deps

    def validate_contract_conformance(
        self, workspace: Path, contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected = _extract_openapi_paths(contract)
        if not expected:
            return {"valid": True, "missing_endpoints": [], "extra_endpoints": []}

        route_pattern = self._ROUTE_PATTERNS.get("spring")
        quarkus_route_pattern = self._ROUTE_PATTERNS.get("quarkus")
        if not route_pattern and not quarkus_route_pattern:
            return {"valid": True, "missing_endpoints": [], "extra_endpoints": [],
                    "note": "no Java route patterns configured"}

        class_mapping_re = re.compile(
            r"""@(?:RequestMapping|Path)\s*\(\s*(?:value\s*=\s*)?['"]([^'"]+)['"]"""
        )

        found_routes: Set[str] = set()
        for src in workspace.rglob("*"):
            if not src.is_file() or src.suffix not in self.extensions:
                continue
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            class_prefix = ""
            cm = class_mapping_re.search(content)
            if cm:
                class_prefix = cm.group(1).rstrip("/")
            for m in route_pattern.finditer(content):
                found_routes.add(class_prefix + "/" + m.group(1).lstrip("/"))

        norm = lambda r: re.sub(r"\{[^}]+\}", "{param}", r).rstrip("/") or "/"
        normalised_found = {norm(r) for r in found_routes}
        normalised_expected = {norm(r) for r in expected}

        missing = sorted(normalised_expected - normalised_found)
        extra = sorted(normalised_found - normalised_expected)
        return {"valid": len(missing) == 0, "missing_endpoints": missing, "extra_endpoints": extra}

    def configure_from_yaml(self, config: Dict[str, Any]) -> None:
        entrypoint_cfg = config.get("checks", {}).get("entrypoint", {})
        frameworks = entrypoint_cfg.get("frameworks", {})
        for fw_name, fw_cfg in frameworks.items():
            patterns = []
            for p in fw_cfg.get("patterns", []):
                patterns.append({"pattern": re.compile(p["regex"]), "label": p["label"]})
            if patterns:
                self._FRAMEWORK_WIRING[fw_name] = patterns
            files = fw_cfg.get("files")
            if files:
                self._ENTRYPOINT_FILENAMES[fw_name] = set(files)

    # -- helpers --

    @staticmethod
    def _load_java_third_party_prefixes(workspace: Path) -> set:
        prefixes: set = set()
        pom = workspace / "pom.xml"
        if pom.exists():
            content = pom.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"<groupId>\s*([^<]+?)\s*</groupId>", content):
                prefixes.add(m.group(1) + ".")
        gradle = workspace / "build.gradle"
        if gradle.exists():
            content = gradle.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"['\"]([a-zA-Z0-9_.]+):([a-zA-Z0-9_.-]+):", content):
                prefixes.add(m.group(1) + ".")
        return prefixes

    @staticmethod
    def _java_import_exists(module: str, workspace: Path) -> bool:
        parts = module.split(".")
        file_path = "/".join(parts[:-1]) / Path(parts[-1] + ".java") if len(parts) > 1 else Path(parts[0] + ".java")
        candidate = workspace / file_path
        if candidate.exists():
            return True
        class_file = parts[-1] + ".java"
        for _ in workspace.rglob(class_file):
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# JavaScript / TypeScript strategy
# ═══════════════════════════════════════════════════════════════════════════════

class JavaScriptStrategy(LanguageStrategy):

    @property
    def name(self) -> str:
        return "javascript"

    @property
    def extensions(self) -> Set[str]:
        return {".js", ".jsx", ".ts", ".tsx"}

    _NODE_BUILTINS = frozenset({
        "assert", "async_hooks", "buffer", "child_process", "cluster",
        "console", "constants", "crypto", "dgram", "diagnostics_channel",
        "dns", "domain", "events", "fs", "http", "http2", "https",
        "inspector", "module", "net", "os", "path", "perf_hooks",
        "process", "punycode", "querystring", "readline", "repl",
        "stream", "string_decoder", "sys", "timers", "tls", "trace_events",
        "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
    })

    _FRAMEWORK_WIRING: Dict[str, List[Dict[str, Any]]] = {
        "express": [
            {"pattern": re.compile(r"express\s*\(\s*\)"), "label": "Express app creation"},
            {"pattern": re.compile(r"\.listen\s*\("), "label": "app.listen()"},
        ],
    }

    _ENTRYPOINT_FILENAMES: Dict[str, Set[str]] = {
        "express": {"app.js", "index.js", "server.js", "app.ts", "index.ts", "server.ts"},
    }

    _ROUTE_PATTERNS: Dict[str, re.Pattern] = {
        "express": re.compile(r"""\.(get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)['"]"""),
    }

    _IMPORT_RE = re.compile(
        r"""(?:import\s+.*?\s+from\s+['"](.+?)['"]|require\s*\(\s*['"](.+?)['"]\s*\))"""
    )

    def validate_syntax(self, file_path: Path) -> Dict[str, Any]:
        return _validate_brace_syntax(file_path)

    def validate_imports(self, file_path: Path, workspace: Path) -> Dict[str, Any]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"valid": True, "broken_imports": []}

        npm_packages = self._load_npm_packages(workspace)
        broken: List[Dict[str, Any]] = []

        for lineno, line in enumerate(source.splitlines(), 1):
            for m in self._IMPORT_RE.finditer(line):
                module = m.group(1) or m.group(2)
                if module.startswith("."):
                    if not self._js_relative_import_exists(module, file_path, workspace):
                        broken.append({"module": module, "line": lineno})
                else:
                    pkg_name = module.split("/")[0]
                    if pkg_name.startswith("@"):
                        pkg_name = "/".join(module.split("/")[:2])
                    if pkg_name not in npm_packages and pkg_name not in self._NODE_BUILTINS:
                        if not self._js_relative_import_exists("./" + module, file_path, workspace):
                            broken.append({"module": module, "line": lineno})

        return {"valid": len(broken) == 0, "broken_imports": broken}

    def extract_exports(self, file_path: Path) -> Dict[str, Any]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"file": str(file_path), "type": "js", "exports": {"default": False, "named": []}}

        named: Set[str] = set()
        has_default = bool(re.search(r"export\s+default\b", source))
        if re.search(r"module\.exports\s*=", source):
            has_default = True

        for m in re.finditer(
            r"export\s+(?:async\s+)?(?:function\*?\s+|class\s+|const\s+|let\s+|var\s+)(\w+)", source
        ):
            named.add(m.group(1))

        for m in re.finditer(r"export\s*\{([^}]+)\}", source):
            for name in m.group(1).split(","):
                name = name.strip()
                if " as " in name:
                    name = name.split(" as ")[-1].strip()
                if name:
                    named.add(name)

        return {"file": str(file_path), "type": "js", "exports": {"default": has_default, "named": sorted(named)}}

    def validate_package_structure(self, workspace: Path) -> Dict[str, Any]:
        issues: List[str] = []
        for src in workspace.rglob("*"):
            if not src.is_file() or src.suffix not in self.extensions:
                continue
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in self._IMPORT_RE.finditer(content):
                module = m.group(1) or m.group(2)
                if module.startswith(".") and module.endswith("/"):
                    target = src.parent / module.lstrip("./")
                    if target.is_dir():
                        index_found = any(
                            (target / f"index{ext}").exists()
                            for ext in (".js", ".ts", ".tsx", ".jsx")
                        )
                        if not index_found:
                            issues.append(f"{target.relative_to(workspace)}: missing index.js/ts")
        return {"valid": len(issues) == 0, "issues": issues}

    def validate_entrypoint(self, workspace: Path, tech_stack: str) -> Dict[str, Any]:
        lower = tech_stack.lower()
        framework = ""
        for fw in ("express",):
            if fw in lower:
                framework = fw
                break
        if not framework:
            return {"valid": True, "framework": "", "missing_wiring": [],
                    "note": "no recognised JS framework in tech stack"}

        expected = self._ENTRYPOINT_FILENAMES.get(framework, set())
        rules = self._FRAMEWORK_WIRING.get(framework, [])

        combined = ""
        for src in workspace.rglob("*"):
            if src.is_file() and src.name in expected:
                try:
                    combined += src.read_text(encoding="utf-8", errors="replace") + "\n"
                except Exception:
                    pass

        if not combined:
            return {
                "valid": False, "framework": framework,
                "missing_wiring": [f"No entrypoint file found (expected one of: {', '.join(sorted(expected))})"],
            }
        missing = [r["label"] for r in rules if not r["pattern"].search(combined)]
        return {"valid": len(missing) == 0, "framework": framework, "missing_wiring": missing}

    def load_declared_dependencies(self, workspace: Path) -> Set[str]:
        names: set = set()
        pkg_file = workspace / "package.json"
        if pkg_file.exists():
            try:
                data = _json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    if key in data:
                        names.update(data[key].keys())
            except Exception:
                pass
        return names

    def validate_contract_conformance(
        self, workspace: Path, contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected = _extract_openapi_paths(contract)
        if not expected:
            return {"valid": True, "missing_endpoints": [], "extra_endpoints": []}

        route_pattern = self._ROUTE_PATTERNS.get("express")
        if not route_pattern:
            return {"valid": True, "missing_endpoints": [], "extra_endpoints": []}

        found_routes: Set[str] = set()
        for src in workspace.rglob("*"):
            if not src.is_file() or src.suffix not in self.extensions:
                continue
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in route_pattern.finditer(content):
                found_routes.add(m.group(2))

        norm = lambda r: re.sub(r":[a-zA-Z_]\w*", "{param}", r).rstrip("/") or "/"
        normalised_found = {norm(r) for r in found_routes}
        normalised_expected = {
            re.sub(r"\{[^}]+\}", "{param}", r).rstrip("/") or "/"
            for r in expected
        }

        missing = sorted(normalised_expected - normalised_found)
        extra = sorted(normalised_found - normalised_expected)
        return {"valid": len(missing) == 0, "missing_endpoints": missing, "extra_endpoints": extra}

    def configure_from_yaml(self, config: Dict[str, Any]) -> None:
        entrypoint_cfg = config.get("checks", {}).get("entrypoint", {})
        frameworks = entrypoint_cfg.get("frameworks", {})
        for fw_name, fw_cfg in frameworks.items():
            patterns = []
            for p in fw_cfg.get("patterns", []):
                patterns.append({"pattern": re.compile(p["regex"]), "label": p["label"]})
            if patterns:
                self._FRAMEWORK_WIRING[fw_name] = patterns
            files = fw_cfg.get("files")
            if files:
                self._ENTRYPOINT_FILENAMES[fw_name] = set(files)

    # -- helpers --

    @staticmethod
    def _load_npm_packages(workspace: Path) -> set:
        """Load npm package names from all package.json files in the workspace.

        Scans the root and one level of subdirectories (e.g. frontend/,
        backend/) so that monorepo / multi-project layouts are covered.
        """
        names: set = set()
        candidates = [workspace / "package.json"]
        for child in workspace.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                candidates.append(child / "package.json")
        for pkg_file in candidates:
            if pkg_file.exists():
                try:
                    data = _json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
                    for key in ("dependencies", "devDependencies", "peerDependencies"):
                        if key in data:
                            names.update(data[key].keys())
                except Exception:
                    pass
        return names

    @staticmethod
    def _js_relative_import_exists(module: str, source_file: Path, workspace: Path) -> bool:
        base_dir = source_file.parent
        candidate_base = (base_dir / module).resolve()

        ws_resolved = workspace.resolve()
        try:
            candidate_base.relative_to(ws_resolved)
        except ValueError:
            return False

        extensions = ["", ".js", ".jsx", ".ts", ".tsx"]
        for ext in extensions:
            check = candidate_base.parent / (candidate_base.name + ext)
            if check.exists():
                return True
            # Case-insensitive fallback: scan the directory for a match
            if check.parent.is_dir():
                target_name = (candidate_base.name + ext).lower()
                for sibling in check.parent.iterdir():
                    if sibling.name.lower() == target_name and sibling.is_file():
                        return True

        if candidate_base.is_dir():
            for idx in ("index.js", "index.ts", "index.tsx"):
                if (candidate_base / idx).exists():
                    return True

        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Registry
# ═══════════════════════════════════════════════════════════════════════════════

# Built-in strategies, always available
_BUILTIN_STRATEGIES: Dict[str, type] = {
    "python": PythonStrategy,
    "java": JavaStrategy,
    "javascript": JavaScriptStrategy,
}


class StrategyRegistry:
    """Discovers, configures, and dispatches to language strategies.

    Strategies can be:
    1. Built-in (Python, Java, JavaScript) — always available
    2. YAML-configured — loaded from a config directory to override/extend built-ins
    3. Custom-registered — programmatically added at runtime
    """

    def __init__(self, config_dir: Optional[Path] = None):
        self._strategies: Dict[str, LanguageStrategy] = {}
        self._ext_map: Dict[str, LanguageStrategy] = {}

        for name, cls in _BUILTIN_STRATEGIES.items():
            self.register(cls())

        if config_dir and config_dir.is_dir():
            self._load_yaml_configs(config_dir)

    def register(self, strategy: LanguageStrategy) -> None:
        """Register a strategy instance, indexing it by name and extensions."""
        self._strategies[strategy.name] = strategy
        for ext in strategy.extensions:
            self._ext_map[ext] = strategy

    def get_by_name(self, name: str) -> Optional[LanguageStrategy]:
        return self._strategies.get(name)

    def get_by_extension(self, ext: str) -> Optional[LanguageStrategy]:
        return self._ext_map.get(ext)

    def get_for_file(self, file_path: Path) -> Optional[LanguageStrategy]:
        """Return the strategy for a given file, based on its extension."""
        return self._ext_map.get(Path(file_path).suffix)

    def detect_from_tech_stack(self, tech_stack: str) -> List[LanguageStrategy]:
        """Return strategies relevant to a project based on tech_stack.md content.

        Returns all matching strategies (e.g. a fullstack project may have
        both Python and JavaScript strategies).
        """
        lower = tech_stack.lower()
        result: List[LanguageStrategy] = []

        py_keywords = {"python", "flask", "fastapi", "django", "sqlalchemy", "pip", "requirements.txt", "pyproject.toml"}
        java_keywords = {"java", "kotlin", "spring boot", "spring", "quarkus", "maven", "gradle", "pom.xml", "build.gradle"}
        js_keywords = {"javascript", "typescript", "node", "express", "react", "vue", "angular", "npm", "package.json", "vite"}

        if any(kw in lower for kw in py_keywords):
            s = self._strategies.get("python")
            if s:
                result.append(s)
        if any(kw in lower for kw in java_keywords):
            s = self._strategies.get("java")
            if s:
                result.append(s)
        if any(kw in lower for kw in js_keywords):
            s = self._strategies.get("javascript")
            if s:
                result.append(s)

        return result

    def detect_primary_from_tech_stack(self, tech_stack: str) -> Optional[LanguageStrategy]:
        """Return the primary (backend) strategy for the project."""
        strategies = self.detect_from_tech_stack(tech_stack)
        return strategies[0] if strategies else None

    def is_fullstack(self, tech_stack: str) -> bool:
        """Return True if the tech stack includes both a backend and frontend framework."""
        lower = tech_stack.lower()
        backend_keywords = {"flask", "fastapi", "django", "express", "spring boot", "spring", "quarkus"}
        frontend_keywords = {"react", "vue", "angular", "svelte", "next.js", "nuxt"}
        has_backend = any(kw in lower for kw in backend_keywords)
        has_frontend = any(kw in lower for kw in frontend_keywords)
        return has_backend and has_frontend

    @property
    def all_strategies(self) -> Dict[str, LanguageStrategy]:
        return dict(self._strategies)

    def _load_yaml_configs(self, config_dir: Path) -> None:
        """Load YAML strategy configs from a directory.

        Each ``*.yaml`` / ``*.yml`` file in the directory can override
        framework wiring, entrypoint filenames, and route patterns for
        an existing built-in strategy via ``configure_from_yaml()``.
        """
        for cfg_path in sorted(config_dir.glob("*.y*ml")):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                if not isinstance(config, dict):
                    continue
                lang = config.get("language", cfg_path.stem)
                strategy = self._strategies.get(lang)
                if strategy:
                    strategy.configure_from_yaml(config)
                    logger.info("Loaded YAML config for %s strategy from %s", lang, cfg_path)
                else:
                    logger.warning("No built-in strategy for language '%s' (from %s)", lang, cfg_path)
            except Exception as e:
                logger.warning("Failed to load strategy config %s: %s", cfg_path, e)
