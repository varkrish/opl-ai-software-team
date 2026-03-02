"""
Code Completeness Validator

Detects stub files, placeholder components, truncated output,
TODO-only implementations, syntax errors, and broken imports
that indicate incomplete or broken code generation.
"""
import ast
import re
import sys
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

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
                    if pkg_name not in npm_packages:
                        if not cls._js_relative_import_exists("./" + module, path, ws):
                            pass  # bare specifier not in package.json — could be a built-in or alias

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
        """Check if a dotted Python module path resolves to a file in the workspace."""
        parts = module_path.split(".")
        for depth in range(len(parts), 0, -1):
            sub = parts[:depth]
            candidate = workspace / "/".join(sub[:-1]) / (sub[-1] + ".py") if len(sub) > 1 else workspace / (sub[0] + ".py")
            if candidate.exists():
                return True
            pkg_dir = workspace / "/".join(sub)
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                return True
        return False

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
