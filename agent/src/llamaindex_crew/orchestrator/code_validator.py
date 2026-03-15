"""
Code Completeness Validator

Detects stub files, placeholder components, truncated output,
TODO-only implementations, syntax errors, broken imports,
undeclared dependencies, and tech-stack conflicts
that indicate incomplete or broken code generation.
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

logger = logging.getLogger(__name__)

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

    _BRACE_LANGS = {".java", ".js", ".jsx", ".ts", ".tsx", ".go", ".kt", ".swift", ".c", ".cpp", ".h", ".rs"}
    _JS_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
    _JAVA_EXTENSIONS = {".java", ".kt"}

    @classmethod
    def validate_syntax(cls, file_path: Path) -> Dict[str, Any]:
        """Check source file for syntax errors.

        - Python: uses ``ast.parse``
        - Java/JS/TS/Go/C/C++/Rust/Kotlin/Swift: checks balanced braces/brackets/parens.

        Returns ``{"valid": bool, "error": str}``.
        """
        path = Path(file_path)
        if path.suffix == ".py":
            return cls._validate_python_syntax(path)
        if path.suffix in cls._BRACE_LANGS:
            return cls._validate_brace_syntax(path)
        return {"valid": True, "error": ""}

    @classmethod
    def _validate_python_syntax(cls, path: Path) -> Dict[str, Any]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=str(path))
            return {"valid": True, "error": ""}
        except SyntaxError as e:
            return {"valid": False, "error": f"SyntaxError at line {e.lineno}: {e.msg}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}

    @classmethod
    def _validate_brace_syntax(cls, path: Path) -> Dict[str, Any]:
        """Check that braces, brackets, and parens are balanced (ignoring strings/comments)."""
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"valid": False, "error": str(e)}

        cleaned = cls._strip_strings_and_comments(source)
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
                    return {"valid": False, "error": f"Mismatched brace: expected '{pairs[opener]}' but got '{ch}' at line {line}"}

        if stack:
            opener, pos = stack[-1]
            line = source[:pos].count("\n") + 1
            return {"valid": False, "error": f"Unmatched opening '{opener}' at line {line} (no matching closing brace)"}

        return {"valid": True, "error": ""}

    @classmethod
    def _strip_strings_and_comments(cls, source: str) -> str:
        """Remove string literals and comments so delimiter counting isn't fooled."""
        result = re.sub(r'//[^\n]*', '', source)
        result = re.sub(r'/\*.*?\*/', '', result, flags=re.DOTALL)
        result = re.sub(r'"(?:[^"\\]|\\.)*"', '""', result)
        result = re.sub(r"'(?:[^'\\]|\\.)*'", "''", result)
        result = re.sub(r'`(?:[^`\\]|\\.)*`', '``', result)
        return result

    @classmethod
    def validate_imports(
        cls,
        file_path: Path,
        workspace_path: Path,
    ) -> Dict[str, Any]:
        """Verify that local imports resolve to files in the workspace.

        Supports Python, Java/Kotlin, and JS/TS.
        Returns ``{"valid": bool, "broken_imports": [{"module": str, "line": int}]}``.
        """
        path = Path(file_path)
        ws = Path(workspace_path)

        if path.suffix == ".py":
            return cls._validate_python_imports(path, ws)
        if path.suffix in cls._JAVA_EXTENSIONS:
            return cls._validate_java_imports(path, ws)
        if path.suffix in cls._JS_EXTENSIONS:
            return cls._validate_js_imports(path, ws)
        return {"valid": True, "broken_imports": []}

    @classmethod
    def _validate_python_imports(cls, path: Path, ws: Path) -> Dict[str, Any]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return {"valid": True, "broken_imports": []}

        third_party = cls._load_third_party_names(ws)
        stdlib = cls._stdlib_names()
        broken: List[Dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in stdlib and top not in third_party:
                        if not cls._module_exists(alias.name, ws):
                            broken.append({"module": alias.name, "line": node.lineno})
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top not in stdlib and top not in third_party:
                        if not cls._module_exists(node.module, ws):
                            broken.append({"module": node.module, "line": node.lineno})

        return {"valid": len(broken) == 0, "broken_imports": broken}

    _JAVA_STDLIB_PREFIXES = frozenset({
        "java.", "javax.", "jakarta.",
        "org.w3c.", "org.xml.", "org.ietf.",
        "sun.", "com.sun.", "jdk.",
    })

    @classmethod
    def _validate_java_imports(cls, path: Path, ws: Path) -> Dict[str, Any]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"valid": True, "broken_imports": []}

        third_party = cls._load_java_third_party_prefixes(ws)
        broken: List[Dict[str, Any]] = []

        for lineno, line in enumerate(source.splitlines(), 1):
            m = re.match(r'^\s*import\s+(static\s+)?([a-zA-Z0-9_.]+)\s*;', line)
            if not m:
                continue
            module = m.group(2)

            if any(module.startswith(p) for p in cls._JAVA_STDLIB_PREFIXES):
                continue
            if any(module.startswith(p) for p in third_party):
                continue

            if not cls._java_import_exists(module, ws):
                broken.append({"module": module, "line": lineno})

        return {"valid": len(broken) == 0, "broken_imports": broken}

    @classmethod
    def _validate_js_imports(cls, path: Path, ws: Path) -> Dict[str, Any]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"valid": True, "broken_imports": []}

        npm_packages = cls._load_npm_packages(ws)
        broken: List[Dict[str, Any]] = []

        import_re = re.compile(
            r"""(?:import\s+.*?\s+from\s+['"](.+?)['"]|"""
            r"""require\s*\(\s*['"](.+?)['"]\s*\))"""
        )

        for lineno, line in enumerate(source.splitlines(), 1):
            for m in import_re.finditer(line):
                module = m.group(1) or m.group(2)
                if module.startswith("."):
                    if not cls._js_relative_import_exists(module, path, ws):
                        broken.append({"module": module, "line": lineno})
                else:
                    pkg_name = module.split("/")[0]
                    if pkg_name.startswith("@"):
                        pkg_name = "/".join(module.split("/")[:2])
                    if pkg_name not in npm_packages and pkg_name not in cls._NODE_BUILTINS:
                        if not cls._js_relative_import_exists("./" + module, path, ws):
                            broken.append({"module": module, "line": lineno})

        return {"valid": len(broken) == 0, "broken_imports": broken}

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

    # ── Helpers ───────────────────────────────────────────────────────────────

    @classmethod
    def _stdlib_names(cls) -> set:
        """Return a set of Python stdlib top-level module names."""
        if hasattr(sys, "stdlib_module_names"):
            return sys.stdlib_module_names
        # Fallback for Python < 3.10
        import pkgutil
        return {m.name for m in pkgutil.iter_modules() if m.module_finder is not None} | {
            "os", "sys", "re", "json", "pathlib", "datetime", "typing",
            "collections", "functools", "itertools", "math", "hashlib",
            "logging", "unittest", "tempfile", "shutil", "sqlite3",
            "abc", "io", "copy", "enum", "dataclasses", "contextlib",
            "asyncio", "threading", "subprocess", "uuid", "time",
        }

    @classmethod
    def _load_third_party_names(cls, workspace: Path) -> set:
        """Extract third-party package names from requirements.txt."""
        names: set = set()
        req_file = workspace / "requirements.txt"
        if req_file.exists():
            for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    pkg = re.split(r"[>=<!\[;]", line)[0].strip()
                    if pkg:
                        names.add(pkg.replace("-", "_").lower())
                        names.add(pkg.replace("-", "_"))
                        names.add(pkg.replace("_", "-"))
                        names.add(pkg)
        return names

    @classmethod
    def _module_exists(cls, module_path: str, workspace: Path) -> bool:
        """Check if a dotted Python module path resolves to a file in the workspace.

        Also verifies that every intermediate directory along the path has an
        ``__init__.py`` so the import would actually succeed at runtime.
        """
        parts = module_path.split(".")
        for depth in range(len(parts), 0, -1):
            sub = parts[:depth]
            if len(sub) > 1:
                candidate = workspace / "/".join(sub[:-1]) / (sub[-1] + ".py")
            else:
                candidate = workspace / (sub[0] + ".py")
            if candidate.exists():
                if not cls._intermediate_packages_valid(sub[:-1], workspace):
                    return False
                return True
            pkg_dir = workspace / "/".join(sub)
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                if not cls._intermediate_packages_valid(sub[:-1], workspace):
                    return False
                return True
        return False

    @classmethod
    def _intermediate_packages_valid(cls, dir_parts: List[str], workspace: Path) -> bool:
        """Return True if every intermediate directory has ``__init__.py``."""
        for i in range(len(dir_parts)):
            pkg_dir = workspace / "/".join(dir_parts[: i + 1])
            if pkg_dir.is_dir() and not (pkg_dir / "__init__.py").exists():
                return False
        return True

    # ── Java helpers ──────────────────────────────────────────────────────────

    @classmethod
    def _load_java_third_party_prefixes(cls, workspace: Path) -> set:
        """Extract third-party package group IDs from pom.xml or build.gradle."""
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

    @classmethod
    def _java_import_exists(cls, module: str, workspace: Path) -> bool:
        """Check if a Java import resolves to a .java file in the workspace.

        ``import com.example.model.Task`` -> ``com/example/model/Task.java``
        """
        parts = module.split(".")
        # The last part is the class name
        file_path = "/".join(parts[:-1]) / Path(parts[-1] + ".java") if len(parts) > 1 else Path(parts[0] + ".java")
        candidate = workspace / file_path
        if candidate.exists():
            return True
        # Also search recursively for the file name (class might be in src/main/java/...)
        class_file = parts[-1] + ".java"
        for f in workspace.rglob(class_file):
            return True
        return False

    # ── JS/TS helpers ─────────────────────────────────────────────────────────

    @classmethod
    def _load_npm_packages(cls, workspace: Path) -> set:
        """Extract package names from package.json dependencies."""
        import json as _json
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

    _NODE_BUILTINS = frozenset({
        "assert", "async_hooks", "buffer", "child_process", "cluster",
        "console", "constants", "crypto", "dgram", "diagnostics_channel",
        "dns", "domain", "events", "fs", "http", "http2", "https",
        "inspector", "module", "net", "os", "path", "perf_hooks",
        "process", "punycode", "querystring", "readline", "repl",
        "stream", "string_decoder", "sys", "timers", "tls", "trace_events",
        "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
    })

    @classmethod
    def _js_relative_import_exists(cls, module: str, source_file: Path, workspace: Path) -> bool:
        """Check if a relative JS/TS import resolves to a file.

        Tries the module path directly, then with common extensions.
        """
        base_dir = source_file.parent
        rel_path = module.lstrip("./")
        candidate_base = base_dir / rel_path

        extensions = ["", ".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts", "/index.tsx"]
        for ext in extensions:
            if (candidate_base.parent / (candidate_base.name + ext)).exists():
                return True
            if ext.startswith("/") and (candidate_base / ext.lstrip("/")).exists():
                return True
        return False

    # ── Export extraction ─────────────────────────────────────────────────────

    @classmethod
    def _extract_js_exports(cls, file_path: Path) -> Dict[str, Any]:
        """Extract named and default exports from a JS/TS file."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {"default": False, "named": []}

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

        return {"default": has_default, "named": sorted(named)}

    @classmethod
    def _extract_python_exports(cls, file_path: Path) -> List[str]:
        """Extract exported symbol names from a Python file.

        Checks ``__all__`` first; falls back to public top-level defs.
        """
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            return []

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            return [
                                elt.value
                                for elt in node.value.elts
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                            ]

        exports: List[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    exports.append(node.name)
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    exports.append(node.name)
        return exports

    @classmethod
    def _extract_java_public_types(cls, file_path: Path) -> List[str]:
        """Extract public class/interface/enum names from a Java/Kotlin file."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        types: List[str] = []
        for m in re.finditer(
            r"public\s+(?:abstract\s+|final\s+|static\s+)*(?:class|interface|enum|record)\s+(\w+)",
            source,
        ):
            types.append(m.group(1))
        return types

    @classmethod
    def extract_export_summary(cls, file_path: Path) -> Dict[str, Any]:
        """Build a structured export summary for any supported source file."""
        path = Path(file_path)
        if path.suffix in cls._JS_EXTENSIONS:
            js_exp = cls._extract_js_exports(path)
            return {"file": str(path), "type": "js", "exports": js_exp}
        if path.suffix == ".py":
            py_exp = cls._extract_python_exports(path)
            return {"file": str(path), "type": "python", "exports": py_exp}
        if path.suffix in cls._JAVA_EXTENSIONS:
            java_exp = cls._extract_java_public_types(path)
            return {"file": str(path), "type": "java", "exports": java_exp}
        return {"file": str(path), "type": "unknown", "exports": []}

    # ── Dependency manifest validation ────────────────────────────────────────

    @classmethod
    def _load_python_third_party_names(cls, workspace: Path) -> Set[str]:
        """Load declared Python dependencies from requirements.txt and pyproject.toml."""
        names: Set[str] = set()
        names.update(cls._load_third_party_names(workspace))

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

    @classmethod
    def _load_java_declared_dependencies(cls, workspace: Path) -> Set[str]:
        """Load declared Java dependencies as ``groupId:artifactId`` strings."""
        deps: Set[str] = set()
        pom = workspace / "pom.xml"
        if pom.exists():
            content = pom.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(
                r"<groupId>\s*([^<]+?)\s*</groupId>\s*<artifactId>\s*([^<]+?)\s*</artifactId>",
                content,
                re.DOTALL,
            ):
                deps.add(f"{m.group(1)}:{m.group(2)}")
        for gf in ("build.gradle", "build.gradle.kts"):
            gradle = workspace / gf
            if gradle.exists():
                content = gradle.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r"['\"]([a-zA-Z0-9_.]+):([a-zA-Z0-9_.-]+):", content):
                    deps.add(f"{m.group(1)}:{m.group(2)}")
        return deps

    @classmethod
    def validate_dependency_manifest(cls, workspace_path: Path) -> Dict[str, Any]:
        """Validate that all imported packages are declared in the project manifest.

        Scans JS/TS (package.json), Python (requirements.txt / pyproject.toml),
        and Java (pom.xml / build.gradle) source files.

        Returns ``{"valid": bool, "missing": [{"ecosystem": str, "package": str, "files": [str]}]}``.
        """
        ws = Path(workspace_path)
        missing: Dict[str, set] = {}  # package -> set of files

        # ── JS/TS ────────────────────────────────────────────────────────
        npm_packages = cls._load_npm_packages(ws)
        has_pkg_json = (ws / "package.json").exists()
        import_re = re.compile(
            r"""(?:import\s+.*?\s+from\s+['"](.+?)['"]|require\s*\(\s*['"](.+?)['"]\s*\))"""
        )
        for src in ws.rglob("*"):
            if not src.is_file() or src.suffix not in cls._JS_EXTENSIONS:
                continue
            try:
                source = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line in source.splitlines():
                for m in import_re.finditer(line):
                    module = m.group(1) or m.group(2)
                    if module.startswith("."):
                        continue
                    pkg = module.split("/")[0]
                    if pkg.startswith("@"):
                        pkg = "/".join(module.split("/")[:2])
                    if pkg in npm_packages or pkg in cls._NODE_BUILTINS:
                        continue
                    key = f"npm:{pkg}"
                    missing.setdefault(key, set()).add(str(src.relative_to(ws)))

        # ── Python ───────────────────────────────────────────────────────
        py_tp = cls._load_python_third_party_names(ws)
        stdlib = cls._stdlib_names()
        for src in ws.rglob("*.py"):
            if not src.is_file():
                continue
            try:
                tree = ast.parse(src.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            for node in ast.walk(tree):
                top = None
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        top = node.module.split(".")[0]
                if top and top not in stdlib and top not in py_tp:
                    if not cls._module_exists(top, ws):
                        key = f"pypi:{top}"
                        missing.setdefault(key, set()).add(str(src.relative_to(ws)))

        # ── Java ─────────────────────────────────────────────────────────
        java_tp_prefixes = cls._load_java_third_party_prefixes(ws)
        for src in ws.rglob("*"):
            if not src.is_file() or src.suffix not in cls._JAVA_EXTENSIONS:
                continue
            try:
                source = src.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line in source.splitlines():
                m = re.match(r"^\s*import\s+(?:static\s+)?([a-zA-Z0-9_.]+)\s*;", line)
                if not m:
                    continue
                module = m.group(1)
                if any(module.startswith(p) for p in cls._JAVA_STDLIB_PREFIXES):
                    continue
                if any(module.startswith(p) for p in java_tp_prefixes):
                    continue
                if cls._java_import_exists(module, ws):
                    continue
                group_prefix = ".".join(module.split(".")[:2])
                key = f"maven:{group_prefix}"
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
        """Detect the chosen tech stack from tech_stack.md content.

        Returns a dict mapping ecosystem to the set of chosen library keywords.
        """
        lower = tech_stack_content.lower()
        chosen: Dict[str, Set[str]] = {"js": set(), "python": set(), "java": set()}
        js_kw = {
            "express": "express", "fastify": "fastify", "koa": "koa",
            "vite": "vite", "create-react-app": "react-scripts", "cra": "react-scripts",
            "mongoose": "mongoose", "mongodb": "mongodb",
            "sequelize": "sequelize", "typeorm": "typeorm", "prisma": "prisma",
            "knex": "knex",
        }
        py_kw = {
            "django": "django", "flask": "flask", "fastapi": "fastapi",
            "sqlalchemy": "sqlalchemy",
        }
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
        """Validate that generated code only uses libraries from the chosen tech stack.

        Returns ``{"valid": bool, "conflicts": [{"file": str, "conflict": str, "detail": str}]}``.
        """
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
                    files = list(ws.rglob("*"))
                    files = [f for f in files if f.is_file() and f.suffix in cls._JS_EXTENSIONS]
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
        """Check if a package exists in its ecosystem registry (npm / PyPI / Maven Central).

        Results are cached in-memory for the lifetime of the process.
        """
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
            return True  # unknown ecosystem — assume exists

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
            exists = True  # network error — don't block generation
        except Exception:
            exists = True

        cls._LIB_CACHE[cache_key] = exists
        if not exists:
            logger.warning("Library '%s' not found on %s registry", package_name, ecosystem)
        return exists

    @classmethod
    def verify_workspace_libraries(cls, workspace_path: Path) -> Dict[str, Any]:
        """Check that all declared dependencies actually exist in their registries.

        Returns ``{"valid": bool, "hallucinated": [{"ecosystem": str, "package": str}]}``.
        """
        ws = Path(workspace_path)
        hallucinated: List[Dict[str, str]] = []

        # npm
        npm_pkgs = cls._load_npm_packages(ws)
        for pkg in npm_pkgs:
            if not cls.verify_library_exists(pkg, "npm"):
                hallucinated.append({"ecosystem": "npm", "package": pkg})

        # Python
        py_pkgs = cls._load_python_third_party_names(ws) - cls._stdlib_names()
        for pkg in py_pkgs:
            canonical = pkg.replace("_", "-").lower()
            if canonical and not cls.verify_library_exists(canonical, "pypi"):
                hallucinated.append({"ecosystem": "pypi", "package": pkg})

        # Java
        java_deps = cls._load_java_declared_dependencies(ws)
        for dep in java_deps:
            if not cls.verify_library_exists(dep, "maven"):
                hallucinated.append({"ecosystem": "maven", "package": dep})

        return {"valid": len(hallucinated) == 0, "hallucinated": hallucinated}

    # ── Package structure validation ──────────────────────────────────────────

    @classmethod
    def validate_package_structure(cls, workspace_path: Path) -> Dict[str, Any]:
        """Verify Python directories used as import targets have ``__init__.py``.

        Scans all ``.py`` files for ``from <pkg>.<mod> import ...`` patterns,
        then checks that each intermediate directory has ``__init__.py``.

        Returns ``{"valid": bool, "missing_init": [str]}``.
        """
        ws = Path(workspace_path)
        missing: Set[str] = set()

        for src in ws.rglob("*.py"):
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
                    d = ws / "/".join(pkg_parts[: i + 1])
                    if d.is_dir() and not (d / "__init__.py").exists():
                        missing.add(str(d.relative_to(ws)))

        return {"valid": len(missing) == 0, "missing_init": sorted(missing)}

    # ── Duplicate / scattered file detection ──────────────────────────────────

    @classmethod
    def validate_duplicate_files(cls, workspace_path: Path) -> Dict[str, Any]:
        """Detect source files with the same name under different directory trees.

        A common LLM hallucination is generating ``src/app.py`` AND
        ``todo-api/src/app.py`` — two conflicting versions of the same file.

        Returns ``{"valid": bool, "duplicates": [{"filename": str, "paths": [str]}]}``.
        """
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

    _FRAMEWORK_WIRING: Dict[str, List[Dict[str, Any]]] = {
        "flask": [
            {"pattern": re.compile(r"Flask\s*\(\s*__name__\s*\)"), "label": "Flask app creation"},
            {"pattern": re.compile(r"\.init_app\s*\(|db\s*=\s*SQLAlchemy\s*\(\s*app\s*\)"), "label": "db.init_app() or SQLAlchemy(app)"},
            {"pattern": re.compile(r"@app\.route\s*\(|import\s+routes|from\s+\S*routes\S*\s+import"), "label": "route registration or import"},
        ],
        "express": [
            {"pattern": re.compile(r"express\s*\(\s*\)"), "label": "Express app creation"},
            {"pattern": re.compile(r"\.listen\s*\("), "label": "app.listen()"},
        ],
        "fastapi": [
            {"pattern": re.compile(r"FastAPI\s*\("), "label": "FastAPI app creation"},
            {"pattern": re.compile(r"\.include_router\s*\(|@app\.(get|post|put|delete|patch)\s*\("), "label": "router or route registration"},
        ],
        "django": [
            {"pattern": re.compile(r"INSTALLED_APPS\s*="), "label": "settings INSTALLED_APPS"},
            {"pattern": re.compile(r"urlpatterns\s*="), "label": "urlpatterns"},
        ],
        "spring": [
            {"pattern": re.compile(r"@SpringBootApplication"), "label": "@SpringBootApplication"},
            {"pattern": re.compile(r"SpringApplication\.run\s*\("), "label": "SpringApplication.run()"},
        ],
    }

    _ENTRYPOINT_FILENAMES = {
        "flask": {"app.py", "main.py", "server.py", "wsgi.py", "__init__.py"},
        "express": {"app.js", "index.js", "server.js", "app.ts", "index.ts", "server.ts"},
        "fastapi": {"main.py", "app.py", "server.py"},
        "django": {"settings.py", "urls.py", "manage.py"},
        "spring": {"Application.java", "App.java"},
    }

    @classmethod
    def validate_entrypoint(
        cls, workspace_path: Path, tech_stack_content: str
    ) -> Dict[str, Any]:
        """Check that the generated entrypoint file properly wires up the framework.

        Uses the tech-stack document to detect which framework is chosen, then
        verifies that the expected wiring patterns appear in the entrypoint
        file(s).  Works for Flask, Express, FastAPI, Django, and Spring Boot.

        Returns ``{"valid": bool, "framework": str, "missing_wiring": [str]}``.
        """
        ws = Path(workspace_path)
        lower_stack = tech_stack_content.lower()

        framework = ""
        for fw in ("flask", "fastapi", "django", "express", "spring"):
            if fw in lower_stack:
                framework = fw
                break

        if not framework:
            return {"valid": True, "framework": "", "missing_wiring": [],
                    "note": "no recognised framework in tech stack"}

        expected_filenames = cls._ENTRYPOINT_FILENAMES.get(framework, set())
        wiring_rules = cls._FRAMEWORK_WIRING.get(framework, [])

        # Collect content from all candidate entrypoint files
        combined_content = ""
        for src in ws.rglob("*"):
            if src.is_file() and src.name in expected_filenames:
                try:
                    combined_content += src.read_text(encoding="utf-8", errors="replace") + "\n"
                except Exception:
                    pass

        if not combined_content:
            return {
                "valid": False,
                "framework": framework,
                "missing_wiring": [f"No entrypoint file found (expected one of: {', '.join(sorted(expected_filenames))})"],
            }

        missing = [
            r["label"]
            for r in wiring_rules
            if not r["pattern"].search(combined_content)
        ]

        return {
            "valid": len(missing) == 0,
            "framework": framework,
            "missing_wiring": missing,
        }
