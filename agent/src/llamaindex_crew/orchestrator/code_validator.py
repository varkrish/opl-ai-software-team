"""
Code Completeness Validator

Detects stub files, placeholder components, truncated output,
and TODO-only implementations that indicate incomplete code generation.
"""
import re
import logging
from pathlib import Path
from typing import Dict, List, Any

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
