"""The refinement prompt's symbol map was always empty.

``build_symbol_map`` invoked ``tldr structure <path> --lang all``. tldr has no
``all`` language: argparse rejects it, the process exits 2, stdout is empty, and
the function returns "" through its own error handling. Silently. Every time.

The cost lands exactly where a sovereign deployment can least afford it. The map
exists so the model knows which symbols exist BEFORE calling code_search — the
docstring says so — and a 14b that cannot see the symbol list burns retries
guessing names. The feature was built, wired in, and never fired.

Two things kept it invisible: the failure path is indistinguishable from
"workspace has no source", and nothing tested this function at all.

Coverage note: a single ``tldr structure`` call returns ONE language's files on
older tldr, so a full-stack workspace needs the languages enumerated or half the
symbols go missing — the same reason the wiring symbol diff enumerates them.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.tools.tldr_tools import _resolve_tldr_bin, build_symbol_map


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestBuildSymbolMap(unittest.TestCase):
    def setUp(self):
        if not _resolve_tldr_bin():
            self.skipTest("tldr binary not available")

    def test_never_passes_an_invalid_lang_value(self):
        """
        The regression itself. `--lang all` is not a valid tldr language, so
        every call exited 2 and the map came back empty. Assert on the argv the
        function actually builds rather than on the output, so this fails for
        the right reason if it ever comes back.
        """
        import subprocess as real_subprocess

        calls = []
        original = real_subprocess.run

        def spy(cmd, *a, **kw):
            if isinstance(cmd, list):
                calls.append(cmd)
            return original(cmd, *a, **kw)

        with patch("llamaindex_crew.tools.tldr_tools.subprocess.run", side_effect=spy):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                _write(Path(tmp), "app/service.py", "class TaskService:\n    pass\n")
                build_symbol_map(Path(tmp))

        structure_calls = [c for c in calls if "structure" in c]
        self.assertTrue(structure_calls, "expected at least one tldr structure call")
        for cmd in structure_calls:
            if "--lang" in cmd:
                self.assertNotEqual(
                    cmd[cmd.index("--lang") + 1], "all",
                    "'all' is not a tldr language; the call exits 2 and yields nothing",
                )

    def test_returns_symbols_for_a_python_workspace(self):
        """The point of the function: real symbols, not an empty string."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app/service.py",
                   "class TaskService:\n    def create(self):\n        pass\n")
            _write(root, "app/helpers.py", "def build_task():\n    return 1\n")

            result = build_symbol_map(root)

        self.assertTrue(result, "symbol map must not be empty for a real workspace")
        self.assertIn("service.py", result)
        self.assertIn("TaskService", result)

    def test_covers_every_language_in_a_full_stack_workspace(self):
        """
        A backend-plus-frontend workspace is the normal case here, not the edge
        case. If only one language's files appear, the model gets a symbol map
        that silently omits half the project.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "backend/app/service.py", "class TaskService:\n    pass\n")
            _write(root, "frontend/src/models.ts",
                   "export class TaskModel {\n  id: string = '';\n}\n")

            result = build_symbol_map(root)

        self.assertIn("service.py", result)
        self.assertIn("models.ts", result)

    def test_respects_the_char_budget(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(40):
                _write(root, f"app/mod{i}.py",
                       f"class VeryLongClassNameNumber{i}:\n    pass\n")

            result = build_symbol_map(root, max_chars=300)

        self.assertLessEqual(len(result), 300 + len("\n... (truncated)"))

    def test_empty_workspace_yields_empty_string(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(build_symbol_map(Path(tmp)), "")

    def test_missing_binary_yields_empty_string(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp), "app/service.py", "class A:\n    pass\n")
            with patch(
                "llamaindex_crew.tools.tldr_tools._resolve_tldr_bin", return_value=None,
            ):
                self.assertEqual(build_symbol_map(Path(tmp)), "")


if __name__ == "__main__":
    unittest.main()
