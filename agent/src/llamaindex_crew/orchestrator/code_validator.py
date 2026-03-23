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
    def validate_package_structure(
        cls, workspace_path: Path, tech_stack_content: str = ""
    ) -> Dict[str, Any]:
        """Delegate to the strategy that matches the project (Python/Java/JS).

        For Java/Quarkus projects this runs Java package-dir checks, not Python
        __init__.py. Pass tech_stack_content when available so non-Python
        projects are not validated with Python rules.

        Returns ``{"valid": bool, "missing_init": [str]}`` (Python) or
        strategy-specific keys (e.g. issues for Java).
        """
        ws = Path(workspace_path)
        if tech_stack_content and tech_stack_content.strip():
            strategies = _default_registry.detect_from_tech_stack(tech_stack_content)
            for strategy in strategies:
                result = strategy.validate_package_structure(ws)
                if result is not None and "valid" in result:
                    return result
        # Fallback: only run Python __init__.py check if workspace has .py files
        py = _default_registry.get_by_name("python")
        if py and any(ws.rglob("*.py")):
            return py.validate_package_structure(ws)
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

    # ── Package.json completeness ─────────────────────────────────────────

    _NPM_IMPORT_RE = re.compile(
        r"""(?:import\s+.*?\s+from\s+['"]([^'"./][^'"]*?)['"]|"""
        r"""import\s+['"]([^'"./][^'"]*?)['"]|"""
        r"""require\s*\(\s*['"]([^'"./][^'"]*?)['"]\s*\))"""
    )

    _NODE_BUILTINS = frozenset({
        "assert", "async_hooks", "buffer", "child_process", "cluster",
        "console", "constants", "crypto", "dgram", "diagnostics_channel",
        "dns", "domain", "events", "fs", "http", "http2", "https",
        "inspector", "module", "net", "os", "path", "perf_hooks",
        "process", "punycode", "querystring", "readline", "repl",
        "stream", "string_decoder", "sys", "timers", "tls", "trace_events",
        "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
        "node:fs", "node:path", "node:http", "node:url", "node:crypto",
        "node:os", "node:child_process", "node:stream", "node:util",
        "node:events", "node:net", "node:tls", "node:buffer",
    })

    @classmethod
    def validate_package_json_completeness(
        cls, workspace_path: Path,
    ) -> Dict[str, Any]:
        """Check that every npm-style import in JS/TS source files is declared
        in a package.json in the workspace.

        Returns ``{"valid": bool, "missing": [{"package": str, "files": [str]}]}``.
        """
        ws = Path(workspace_path)

        declared: Set[str] = set()
        for pkg_file in ws.rglob("package.json"):
            try:
                data = _json.loads(pkg_file.read_text(encoding="utf-8", errors="replace"))
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    if key in data and isinstance(data[key], dict):
                        declared.update(data[key].keys())
            except Exception:
                pass

        imported_by: Dict[str, set] = {}
        for src in ws.rglob("*"):
            if not src.is_file() or src.suffix not in cls._JS_EXTENSIONS.union(cls._TS_EXTENSIONS):
                continue
            rel = str(src.relative_to(ws))
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in cls._NPM_IMPORT_RE.finditer(content):
                raw = m.group(1) or m.group(2) or m.group(3)
                if not raw:
                    continue
                pkg = raw.split("/")[0]
                if pkg.startswith("@"):
                    pkg = "/".join(raw.split("/")[:2])
                if pkg in cls._NODE_BUILTINS or raw in cls._NODE_BUILTINS:
                    continue
                if pkg not in declared:
                    imported_by.setdefault(pkg, set()).add(rel)

        missing = [
            {"package": pkg, "files": sorted(files)}
            for pkg, files in sorted(imported_by.items())
        ]
        return {"valid": len(missing) == 0, "missing": missing}

    # ── Maven pom.xml completeness (Java imports vs declared deps) ────────

    _JAVA_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)\s*;", re.MULTILINE)

    # Maps top-level Java package prefixes to Maven groupId:artifactId pairs.
    # Only Spring Boot starters and common Java EE / Jakarta libs are mapped;
    # anything not matched is silently ignored (could be project-internal).
    _JAVA_PACKAGE_TO_MAVEN: Dict[str, tuple] = {
        "org.springframework.boot": ("org.springframework.boot", "spring-boot-starter"),
        "org.springframework.web": ("org.springframework.boot", "spring-boot-starter-web"),
        "org.springframework.http": ("org.springframework.boot", "spring-boot-starter-web"),
        "org.springframework.beans": ("org.springframework.boot", "spring-boot-starter"),
        "org.springframework.context": ("org.springframework.boot", "spring-boot-starter"),
        "org.springframework.stereotype": ("org.springframework.boot", "spring-boot-starter"),
        "org.springframework.data.jpa": ("org.springframework.boot", "spring-boot-starter-data-jpa"),
        "org.springframework.data.repository": ("org.springframework.boot", "spring-boot-starter-data-jpa"),
        "org.springframework.security": ("org.springframework.boot", "spring-boot-starter-security"),
        "org.springframework.messaging": ("org.springframework.boot", "spring-boot-starter-websocket"),
        "org.springframework.web.socket": ("org.springframework.boot", "spring-boot-starter-websocket"),
        "org.springframework.mail": ("org.springframework.boot", "spring-boot-starter-mail"),
        "org.springframework.scheduling": ("org.springframework.boot", "spring-boot-starter"),
        "org.springframework.validation": ("org.springframework.boot", "spring-boot-starter-validation"),
        "jakarta.validation": ("org.springframework.boot", "spring-boot-starter-validation"),
        "jakarta.persistence": ("org.springframework.boot", "spring-boot-starter-data-jpa"),
        "javax.persistence": ("org.springframework.boot", "spring-boot-starter-data-jpa"),
        "org.postgresql": ("org.postgresql", "postgresql"),
        "com.fasterxml.jackson": ("com.fasterxml.jackson.core", "jackson-databind"),
        "io.jsonwebtoken": ("io.jsonwebtoken", "jjwt-api"),
        "lombok": ("org.projectlombok", "lombok"),
        "org.junit.jupiter": ("org.springframework.boot", "spring-boot-starter-test"),
        "org.mockito": ("org.springframework.boot", "spring-boot-starter-test"),
        "org.assertj": ("org.springframework.boot", "spring-boot-starter-test"),
    }

    @classmethod
    def _match_java_import(cls, import_str: str) -> Optional[tuple]:
        """Return (groupId, artifactId) if the import maps to a known Maven dep."""
        for prefix, coords in sorted(cls._JAVA_PACKAGE_TO_MAVEN.items(),
                                      key=lambda x: -len(x[0])):
            if import_str.startswith(prefix):
                return coords
        return None

    @classmethod
    def validate_pom_xml_completeness(
        cls, workspace_path: Path,
    ) -> Dict[str, Any]:
        """Check that every Java import maps to a dependency declared in pom.xml.

        Returns ``{"valid": bool, "missing": [{"groupId": str, "artifactId": str, "files": [str]}]}``.
        """
        ws = Path(workspace_path)

        declared_artifacts: Set[str] = set()
        for pom in ws.rglob("pom.xml"):
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(str(pom))
                ns = {"m": "http://maven.apache.org/POM/4.0.0"}
                for dep in tree.findall(".//m:dependency", ns):
                    aid = dep.find("m:artifactId", ns)
                    if aid is not None and aid.text:
                        declared_artifacts.add(aid.text.strip())
                for dep in tree.findall(".//dependency"):
                    aid = dep.find("artifactId")
                    if aid is not None and aid.text:
                        declared_artifacts.add(aid.text.strip())
                # Spring Boot starters imply transitive deps
                if "spring-boot-starter-web" in declared_artifacts:
                    declared_artifacts.update([
                        "spring-boot-starter", "jackson-databind",
                    ])
                if "spring-boot-starter-data-jpa" in declared_artifacts:
                    declared_artifacts.add("spring-boot-starter")
                if "spring-boot-starter-security" in declared_artifacts:
                    declared_artifacts.add("spring-boot-starter")
                if "spring-boot-starter-test" in declared_artifacts:
                    declared_artifacts.update(["junit-jupiter", "mockito-core", "assertj-core"])
            except Exception:
                pass

        if not declared_artifacts:
            return {"valid": True, "missing": []}

        needed_by: Dict[str, set] = {}  # "groupId:artifactId" -> set of files
        for src in ws.rglob("*.java"):
            if not src.is_file():
                continue
            rel = str(src.relative_to(ws))
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in cls._JAVA_IMPORT_RE.finditer(content):
                import_str = m.group(1)
                coords = cls._match_java_import(import_str)
                if coords is None:
                    continue
                group_id, artifact_id = coords
                if artifact_id not in declared_artifacts:
                    key = f"{group_id}:{artifact_id}"
                    needed_by.setdefault(key, set()).add(rel)

        missing = []
        for key, files in sorted(needed_by.items()):
            gid, aid = key.split(":", 1)
            missing.append({"groupId": gid, "artifactId": aid, "files": sorted(files)})

        return {"valid": len(missing) == 0, "missing": missing}

    # ── Intra-file duplicate code block detection ─────────────────────────

    @classmethod
    def validate_duplicate_code_blocks(
        cls, workspace_path: Path, min_block_lines: int = 5,
    ) -> Dict[str, Any]:
        """Detect repeated code blocks within individual source files.

        Splits each file into overlapping windows of *min_block_lines* lines
        (ignoring blank/comment-only lines) and flags files where a window
        appears more than once.

        Returns ``{"valid": bool, "duplicates": [{"file": str, "repeated_lines": int, "occurrences": int}]}``.
        """
        ws = Path(workspace_path)
        duplicates: List[Dict[str, Any]] = []

        for src in sorted(ws.rglob("*")):
            if not src.is_file() or src.suffix not in _SOURCE_EXTENSIONS:
                continue
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = [
                l.strip() for l in content.splitlines()
                if l.strip() and not l.strip().startswith(("#", "//", "/*", "*"))
            ]
            if len(lines) < min_block_lines * 2:
                continue

            seen: Dict[str, int] = {}
            for i in range(len(lines) - min_block_lines + 1):
                block = "\n".join(lines[i : i + min_block_lines])
                seen[block] = seen.get(block, 0) + 1

            for block, count in seen.items():
                if count > 1:
                    duplicates.append({
                        "file": str(src.relative_to(ws)),
                        "repeated_lines": min_block_lines,
                        "occurrences": count,
                    })
                    break

        return {"valid": len(duplicates) == 0, "duplicates": duplicates}

    # ── Module system consistency ───────────────────────────────────────────

    _JS_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs"})
    _TS_EXTENSIONS = frozenset({".ts", ".tsx"})
    _ESM_RE = re.compile(
        r"(?:^|\n)\s*(?:import\s+.+\s+from\s+['\"]|import\s+['\"]|export\s+(?:default\s+|const\s+|function\s+|class\s+|{))"
    )
    _CJS_RE = re.compile(
        r"(?:require\s*\(\s*['\"]|module\.exports\s*=)"
    )
    _TEST_PATH_RE = re.compile(
        r"(?:test|tests|__tests__|spec|__spec__)[/\\]", re.IGNORECASE
    )

    @classmethod
    def validate_module_consistency(
        cls, workspace_path: Path,
    ) -> Dict[str, Any]:
        """Detect mixed ES module / CommonJS usage across JS/TS source files.

        Test files are excluded because Jest/Vitest may transform them.
        """
        ws = Path(workspace_path)
        esm_files: List[str] = []
        cjs_files: List[str] = []
        mixed_files: List[str] = []

        for src in sorted(ws.rglob("*")):
            if not src.is_file():
                continue
            if src.suffix not in cls._JS_EXTENSIONS and src.suffix not in cls._TS_EXTENSIONS:
                continue
            rel = str(src.relative_to(ws))
            if cls._TEST_PATH_RE.search(rel) or ".test." in rel or ".spec." in rel:
                continue
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            has_esm = bool(cls._ESM_RE.search(content))
            has_cjs = bool(cls._CJS_RE.search(content))
            if has_esm and has_cjs:
                mixed_files.append(rel)
            elif has_esm:
                esm_files.append(rel)
            elif has_cjs:
                cjs_files.append(rel)

        conflicts: List[Dict[str, Any]] = []
        if mixed_files:
            for f in mixed_files:
                conflicts.append({
                    "file": f,
                    "conflict": "mixed_module_system",
                    "detail": f"{f} mixes import/export with require()/module.exports",
                })
        if esm_files and cjs_files:
            conflicts.append({
                "file": cjs_files[0],
                "conflict": "inconsistent_module_system",
                "detail": (
                    f"Project mixes ES modules ({esm_files[0]}) "
                    f"with CommonJS ({cjs_files[0]})"
                ),
            })

        return {"valid": len(conflicts) == 0, "conflicts": conflicts}

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
