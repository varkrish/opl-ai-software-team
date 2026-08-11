"""One compile-feedback pass right after generation, then hand off.

Generation finishes with nobody having tried to compile the result. The first
build attempt happens inside the post-build validation loop, by which point the
model is reasoning about its code from a cold start, several phases removed from
writing it.

Giving the model compile access is the best-performing option and the one a
sovereign 14b deployment can least afford: a compile tool plus a ReAct loop
means repeated tool-call parsing, and the ReAct parser already fails on this
model tier — TechArchitect runs deliberately tool-less for exactly that reason.

So this takes the cheap part of the benefit and none of the cost: compile once,
scaffold what is deterministically missing, hand the compiler's own errors back
for ONE revision pass, and stop. Not a loop. No re-validation, no convergence
check, no second round — the existing post-build loop still runs afterwards and
remains the mechanism that iterates.

Two properties matter most, and both are about bounding:

  - ONE pass. Every test that could pass by looping asserts the call count.
  - ONLY the files the compiler named. The workspace is not the prompt; a 14b
    reading a whole tree finds the error text crowded out by files that
    compiled fine.

Ordering is load-bearing: deterministic scaffolding runs BEFORE the model is
asked anything. javac reports a missing type against the file that REFERENCES
it, so without a stub on disk the model is asked to fix a file that is already
correct — the failure that burned four iterations in job b13dde92.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from llamaindex_crew.workflows.software_dev_workflow import SoftwareDevWorkflow as W


JAVAC_FAILURE = (
    "❌ Build failed\n"
    "[ERROR] /app/src/main/java/com/example/task/TaskService.java:[33,28] cannot find symbol\n"
    "  symbol:   class TaskDto\n"
    "  location: class com.example.task.TaskService\n"
)

GREEN = "✅ Smoke test passed"


def _workflow(tmp_path, *, dev_agent=True):
    wf = W.__new__(W)
    wf.workspace_path = tmp_path
    wf.dev_agent = MagicMock() if dev_agent else None
    wf._wiring_contract = None
    wf.tech_stack = "Java 17, Spring Boot, Maven"
    return wf


def _java_workspace(tmp_path):
    pkg = tmp_path / "src" / "main" / "java" / "com" / "example" / "task"
    pkg.mkdir(parents=True)
    (pkg / "TaskService.java").write_text(
        "package com.example.task;\npublic class TaskService {}\n", encoding="utf-8",
    )
    return tmp_path


class TestBounding(unittest.TestCase):
    """One pass. Never a loop."""

    def test_exactly_one_fix_pass_per_named_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)))
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       return_value=JAVAC_FAILURE) as smoke, \
                 patch.object(wf, "_run_post_build_fix_with_context") as fix:
                wf._run_compile_feedback_turn()

        self.assertEqual(smoke.call_count, 1, "compile exactly once — this is not a loop")
        self.assertEqual(fix.call_count, 1, "one revision pass, no iteration")

    def test_does_not_revalidate_after_fixing(self):
        """
        Re-validating would be the first step of a loop. The post-build loop
        already owns iteration; this pass must hand off unconditionally.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)))
            wf._run_validation_suite = MagicMock()
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       return_value=JAVAC_FAILURE), \
                 patch.object(wf, "_run_post_build_fix_with_context"):
                wf._run_compile_feedback_turn()

        wf._run_validation_suite.assert_not_called()

    def test_only_compiler_named_files_are_sent(self):
        """
        A file that compiled fine must not enter the prompt. The workspace here
        has two files; the compiler named one.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = _java_workspace(Path(tmp))
            pkg = root / "src" / "main" / "java" / "com" / "example" / "task"
            (pkg / "Untouched.java").write_text(
                "package com.example.task;\npublic class Untouched {}\n", encoding="utf-8",
            )
            wf = _workflow(root)
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       return_value=JAVAC_FAILURE), \
                 patch.object(wf, "_run_post_build_fix_with_context") as fix:
                wf._run_compile_feedback_turn()

        targeted = {call.args[0] for call in fix.call_args_list}
        self.assertTrue(any("TaskService.java" in t for t in targeted))
        self.assertFalse(any("Untouched.java" in t for t in targeted))


class TestScaffoldingRunsFirst(unittest.TestCase):
    """Deterministic fixes before the model is asked anything."""

    def test_missing_java_type_is_scaffolded_before_the_model_is_asked(self):
        """
        javac blames the REFERENCING file, so without this the model is told to
        fix TaskService.java — which is correct — and cannot create TaskDto from
        inside it. Four wasted iterations in job b13dde92.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = _java_workspace(Path(tmp))
            stub = (root / "src" / "main" / "java" / "com" / "example"
                    / "task" / "TaskDto.java")
            seen = {}

            def record(*a, **kw):
                seen["stub_existed"] = stub.exists()

            wf = _workflow(root)
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       return_value=JAVAC_FAILURE), \
                 patch.object(wf, "_run_post_build_fix_with_context", side_effect=record):
                wf._run_compile_feedback_turn()

            self.assertTrue(stub.exists(), "stub must be created")
            self.assertTrue(
                seen.get("stub_existed"),
                "stub must exist BEFORE the model is asked to fix anything",
            )


class TestGating(unittest.TestCase):
    """Cheap to switch off, silent when it cannot help."""

    def test_disabled_by_env(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)))
            with patch.dict("os.environ", {"COMPILE_FEEDBACK_TURN": "0"}, clear=False), \
                 patch("llamaindex_crew.tools.test_tools.smoke_test_runner") as smoke, \
                 patch.object(wf, "_run_post_build_fix_with_context") as fix:
                wf._run_compile_feedback_turn()

        smoke.assert_not_called()
        fix.assert_not_called()

    def test_green_build_asks_the_model_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)))
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       return_value=GREEN), \
                 patch.object(wf, "_run_post_build_fix_with_context") as fix:
                wf._run_compile_feedback_turn()

        fix.assert_not_called()

    def test_no_dev_agent_is_a_no_op(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)), dev_agent=False)
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner") as smoke:
                wf._run_compile_feedback_turn()
        smoke.assert_not_called()

    def test_unattributable_failure_asks_nothing(self):
        """
        A build failure with no file attribution — a missing dependency, a
        broken toolchain — has nothing for a per-file fix pass to act on. The
        post-build loop's infrastructure detector handles those.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)))
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       return_value="❌ Could not reach repo.maven.apache.org"), \
                 patch.object(wf, "_run_post_build_fix_with_context") as fix:
                wf._run_compile_feedback_turn()

        fix.assert_not_called()


class TestNeverBlocksTheBuild(unittest.TestCase):
    """This is an optimisation. It must never be the reason a job fails."""

    def test_smoke_runner_raising_is_swallowed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)))
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       side_effect=RuntimeError("sandbox unavailable")), \
                 patch.object(wf, "_run_post_build_fix_with_context") as fix:
                wf._run_compile_feedback_turn()  # must not raise
        fix.assert_not_called()

    def test_fix_pass_raising_is_swallowed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wf = _workflow(_java_workspace(Path(tmp)))
            with patch("llamaindex_crew.tools.test_tools.smoke_test_runner",
                       return_value=JAVAC_FAILURE), \
                 patch.object(wf, "_run_post_build_fix_with_context",
                              side_effect=RuntimeError("LLM down")):
                wf._run_compile_feedback_turn()  # must not raise


if __name__ == "__main__":
    unittest.main()
