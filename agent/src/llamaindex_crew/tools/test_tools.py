"""
Test execution tools for AI agents
Migrated from CrewAI BaseTool to LlamaIndex FunctionTool
"""
import json
import logging
import os
import re
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from llama_index.core.tools import FunctionTool

from .file_tools import _resolve_workspace

logger = logging.getLogger(__name__)


def _read_test_plan(workspace: Path) -> Dict[str, str]:
    """Parse test_plan.md key-value lines. Returns {} if the file is missing."""
    plan_file = workspace / "test_plan.md"
    if not plan_file.is_file():
        return {}
    result: Dict[str, str] = {}
    for line in plan_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if key:
            result[key] = value
    return result


def _find_container_runtime() -> Optional[str]:
    for rt in ("podman", "docker"):
        try:
            subprocess.run([rt, "--version"], capture_output=True, timeout=5)
            return rt
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _run_test_command_in_container(workspace: Path, command: str) -> tuple[int, str]:
    """Run *command* in an isolated container with the workspace mounted at /app."""
    project_type = _detect_project_type(workspace)
    image = CONTAINER_IMAGES.get(project_type)
    if not image:
        return 1, f"No container image for project type '{project_type}'"

    runtime = _find_container_runtime()
    if not runtime:
        return 1, "No container runtime found (tried podman, docker)"

    cmd = [
        runtime, "run", "--rm",
        "--network=none",
        "-v", f"{workspace}:/app:Z",
        "-w", "/app",
        image,
        "sh", "-c", command,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "Test command timed out after 600s"
    except Exception as exc:
        return 1, f"Container test error: {exc}"


def _parse_test_output_with_llm(raw_output: str) -> Dict[str, Any]:
    """Use LLM to parse arbitrary test-runner output into structured results."""
    from ..utils.llm_config import get_llm_for_agent

    prompt = (
        "Parse the test runner output below into JSON with exactly these keys:\n"
        '  "passed" (bool), "total" (int), "passed_count" (int), '
        '"failed_count" (int), "failures" (list of {"test": str, "error": str})\n'
        "Output ONLY valid JSON — no markdown fences, no prose.\n\n"
        f"Test output:\n{raw_output[:8000]}"
    )
    llm = get_llm_for_agent("worker")
    response = str(llm.complete(prompt))

    json_match = re.search(r"\{.*\}", response, re.DOTALL)
    if not json_match:
        passed = "passed" in raw_output.lower() and "failed" not in raw_output.lower()
        return {
            "passed": passed,
            "total": 0,
            "passed_count": 0,
            "failed_count": 0 if passed else 1,
            "failures": [] if passed else [{"test": "unknown", "error": raw_output[:500]}],
        }
    try:
        parsed = json.loads(json_match.group(0))
        parsed.setdefault("passed", parsed.get("failed_count", 0) == 0)
        parsed.setdefault("total", 0)
        parsed.setdefault("passed_count", 0)
        parsed.setdefault("failed_count", 0)
        parsed.setdefault("failures", [])
        return parsed
    except json.JSONDecodeError:
        return {
            "passed": False,
            "total": 0,
            "passed_count": 0,
            "failed_count": 1,
            "failures": [{"test": "parse_error", "error": response[:500]}],
        }


def run_feature_tests(
    layer: str,
    workspace_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run backend or frontend tests from test_plan.md inside a container.

    Gated by SMOKE_TEST_BACKEND — default ``syntax_only`` skips execution.
    """
    if os.getenv("SMOKE_TEST_BACKEND", "syntax_only") == "syntax_only":
        return {"passed": True, "skipped": True, "reason": "syntax_only backend"}

    workspace = Path(workspace_path).resolve() if workspace_path else _resolve_workspace()
    plan = _read_test_plan(workspace)
    if not plan:
        return {"passed": True, "skipped": True, "reason": "no test_plan.md"}

    cmd_key = f"{layer}_test_command"
    command = plan.get(cmd_key, "").strip()
    if not command or command.lower().startswith("echo no"):
        return {"passed": True, "skipped": True, "reason": f"no {cmd_key}"}

    exit_code, raw_output = _run_test_command_in_container(workspace, command)
    parsed = _parse_test_output_with_llm(raw_output)
    if exit_code != 0 and parsed.get("passed"):
        parsed["passed"] = False
        if not parsed.get("failures"):
            parsed["failures"] = [{"test": "runner", "error": raw_output[:500]}]
    parsed["layer"] = layer
    parsed["raw_output"] = raw_output[:4000]
    return parsed


def pytest_runner(
    test_path: str = "tests/",
    verbose: bool = True,
    workspace_path: Optional[str] = None,
) -> str:
    """Run pytest tests in the workspace. Returns test results and coverage.
    
    Args:
        test_path: Path to test directory or file (default: "tests/")
        verbose: Whether to run in verbose mode (default: True)
    
    Returns:
        Test results or error message
    """
    try:
        workspace = Path(workspace_path).resolve() if workspace_path else _resolve_workspace()
        full_path = workspace / test_path
        
        if not full_path.exists():
            return f"❌ Test path not found: {test_path}"
        
        # Build pytest command
        cmd = ["pytest", str(full_path)]
        if verbose:
            cmd.append("-v")
        cmd.extend(["--tb=short", "--color=yes"])
        
        # Run tests
        result = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        output = result.stdout + result.stderr
        
        if result.returncode == 0:
            return f"✅ All tests passed!\n\n{output}"
        else:
            return f"❌ Some tests failed (exit code: {result.returncode})\n\n{output}"
            
    except subprocess.TimeoutExpired:
        return "❌ Tests timed out after 5 minutes"
    except Exception as e:
        return f"❌ Error running tests: {str(e)}"


def code_coverage(
    source_path: str = "src/",
    workspace_path: Optional[str] = None,
) -> str:
    """Run pytest with coverage analysis. Returns coverage percentage and report.
    
    Args:
        source_path: Path to source code directory (default: "src/")
    
    Returns:
        Coverage report or error message
    """
    try:
        workspace = Path(workspace_path).resolve() if workspace_path else _resolve_workspace()
        
        # Build pytest command with coverage
        cmd = [
            "pytest",
            "--cov=" + source_path,
            "--cov-report=term-missing",
            "--cov-report=html",
            "-v"
        ]
        
        # Run tests with coverage
        result = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300
        )
        
        output = result.stdout + result.stderr
        
        # Extract coverage percentage
        for line in output.split('\n'):
            if 'TOTAL' in line and '%' in line:
                return f"📊 Coverage Report:\n\n{output}\n\nCoverage report saved to htmlcov/index.html"
        
        return f"📊 Coverage Report:\n\n{output}"
            
    except subprocess.TimeoutExpired:
        return "❌ Coverage analysis timed out after 5 minutes"
    except Exception as e:
        return f"❌ Error running coverage: {str(e)}"


# ── Smoke test strategy pattern ──────────────────────────────────────────────

_SRC_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go"}

CONTAINER_IMAGES = {
    "node": "registry.access.redhat.com/ubi9/nodejs-20:latest",
    "python": "registry.access.redhat.com/ubi9/python-311:latest",
    "java_maven": "registry.access.redhat.com/ubi9/openjdk-21:latest",
    "java_gradle": "registry.access.redhat.com/ubi9/openjdk-21:latest",
    "go": "registry.access.redhat.com/ubi9/go-toolset:latest",
}

CONTAINER_COMMANDS = {
    "node": "cd /app && npm install --ignore-scripts 2>&1 && node -e \"try{require('./server')}catch(e){process.exit(0)}\"",
    "python": "cd /app && python -m py_compile *.py 2>&1 || true",
    "java_maven": "cd /app && mvn compile -q 2>&1",
    "java_gradle": "cd /app && gradle build -x test -q 2>&1",
    "go": "cd /app && go build ./... 2>&1",
}


def _detect_project_type(workspace: Path) -> str:
    """Auto-detect the project type from manifest files."""
    if (workspace / "pom.xml").exists():
        return "java_maven"
    if (workspace / "build.gradle").exists() or (workspace / "build.gradle.kts").exists():
        return "java_gradle"
    if (workspace / "package.json").exists():
        return "node"
    if (workspace / "requirements.txt").exists() or (workspace / "pyproject.toml").exists():
        return "python"
    if (workspace / "go.mod").exists():
        return "go"
    if list(workspace.rglob("*.java")):
        return "java_maven"
    if list(workspace.rglob("*.py")):
        return "python"
    if list(workspace.rglob("*.go")):
        return "go"
    return "unknown"


class SmokeTestResult:
    """Wraps a smoke test outcome with an optional container / execution log."""

    def __init__(self, message: str, log: str = ""):
        self.message = message
        self.log = log

    def __str__(self) -> str:
        return self.message


class SmokeTestBackend(ABC):
    """Abstract base class for smoke test execution strategies."""

    @abstractmethod
    def run(self, workspace: Path, project_type: str) -> "SmokeTestResult":
        """Run a smoke test and return a SmokeTestResult."""


class SyntaxOnlyBackend(SmokeTestBackend):
    """Static analysis smoke test — no subprocess, no container, safe everywhere.

    Uses CodeCompletenessValidator to check syntax, import resolution,
    and dependency manifest completeness without installing or executing
    any generated code.
    """

    def run(self, workspace: Path, project_type: str) -> str:
        from ..orchestrator.code_validator import CodeCompletenessValidator

        issues: list = []

        for src in sorted(workspace.rglob("*")):
            if not src.is_file() or src.suffix not in _SRC_EXTENSIONS:
                continue
            rel = str(src.relative_to(workspace))

            syn = CodeCompletenessValidator.validate_syntax(src)
            if not syn["valid"]:
                issues.append(f"{rel}: {syn['error']}")

            imp = CodeCompletenessValidator.validate_imports(src, workspace)
            for b in imp["broken_imports"]:
                issues.append(f"{rel}: broken import '{b['module']}' (line {b['line']})")

        manifest = CodeCompletenessValidator.validate_dependency_manifest(workspace)
        for entry in manifest.get("missing", []):
            issues.append(
                f"[{entry['ecosystem']}] undeclared dependency '{entry['package']}'"
            )

        compile_result = CodeCompletenessValidator.run_workspace_compile_gate(workspace)
        for msg in compile_result.get("issues") or []:
            issues.append(f"[compile] {msg}")

        if issues:
            detail = "\n".join(f"  - {i}" for i in issues[:15])
            msg = f"❌ Syntax-only smoke test found {len(issues)} issue(s):\n{detail}"
            return SmokeTestResult(msg, log=detail)
        return SmokeTestResult(f"✅ Syntax-only smoke test passed ({project_type} project)")


class LocalContainerBackend(SmokeTestBackend):
    """Run the smoke test inside an isolated local container (podman/docker).

    Mounts the workspace read-only with ``--network=none`` so generated
    code cannot exfiltrate data.  Falls back from podman to docker.
    """

    TIMEOUT = 180  # 3 minutes

    def run(self, workspace: Path, project_type: str) -> SmokeTestResult:
        image = CONTAINER_IMAGES.get(project_type)
        command = CONTAINER_COMMANDS.get(project_type)
        if not image or not command:
            return SmokeTestResult(f"❌ No container image configured for project type '{project_type}'")

        runtime = self._find_runtime()
        if not runtime:
            return SmokeTestResult("❌ No container runtime found (tried podman, docker)")

        cmd = [
            runtime, "run", "--rm",
            "--network=none",
            "-v", f"{workspace}:/app:Z,ro",
            "-w", "/app",
            image,
            "sh", "-c", command,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.TIMEOUT,
            )
            full_log = self._build_log(runtime, image, cmd, result)
            if result.returncode != 0:
                output = (result.stdout + result.stderr)[:2000]
                return SmokeTestResult(
                    f"❌ Container smoke test failed ({runtime}, {image}):\n{output}",
                    log=full_log,
                )
            return SmokeTestResult(
                f"✅ Container smoke test passed ({runtime}, {image})",
                log=full_log,
            )
        except subprocess.TimeoutExpired:
            return SmokeTestResult(f"❌ Container smoke test timed out after {self.TIMEOUT}s")
        except FileNotFoundError:
            return SmokeTestResult(f"❌ Container runtime '{runtime}' not found on PATH")
        except Exception as e:
            return SmokeTestResult(f"❌ Container smoke test error: {e}")

    @staticmethod
    def _build_log(runtime: str, image: str, cmd: list, result: subprocess.CompletedProcess) -> str:
        lines = [
            f"runtime: {runtime}",
            f"image:   {image}",
            f"command: {' '.join(cmd)}",
            f"exit_code: {result.returncode}",
            "─── stdout ───",
            result.stdout.strip() or "(empty)",
            "─── stderr ───",
            result.stderr.strip() or "(empty)",
        ]
        return "\n".join(lines)

    @staticmethod
    def _find_runtime() -> str | None:
        for rt in ("podman", "docker"):
            try:
                subprocess.run(
                    [rt, "--version"], capture_output=True, timeout=5,
                )
                return rt
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return None


class KubernetesJobBackend(SmokeTestBackend):
    """Run the smoke test as a Kubernetes Job (skeleton for OCP deployment).

    Creates a short-lived Job that mounts the workspace PVC and runs the
    build command in the appropriate ecosystem image.  Polls for completion,
    retrieves logs, then cleans up the Job.

    Requires:
      - ``kubernetes`` Python package (optional dependency)
      - RBAC: ``batch/jobs`` create/get/delete + ``pods/log`` get
      - Workspace PVC accessible from the Job pod (ReadWriteMany recommended)

    Configure via environment variables:
      - SMOKE_TEST_K8S_NAMESPACE  (default: current namespace from SA token)
      - SMOKE_TEST_K8S_PVC        (default: inferred from WORKSPACE_PATH mount)
      - SMOKE_TEST_K8S_TIMEOUT    (default: 180)
    """

    def run(self, workspace: Path, project_type: str) -> SmokeTestResult:
        try:
            from kubernetes import client, config as k8s_config
        except ImportError:
            return SmokeTestResult(
                "❌ kubernetes Python package not installed. "
                "Install with: pip install kubernetes"
            )

        image = CONTAINER_IMAGES.get(project_type)
        command = CONTAINER_COMMANDS.get(project_type)
        if not image or not command:
            return SmokeTestResult(f"❌ No container image configured for project type '{project_type}'")

        namespace = os.getenv("SMOKE_TEST_K8S_NAMESPACE", "")
        pvc_name = os.getenv("SMOKE_TEST_K8S_PVC", "crew-workspace")
        timeout = int(os.getenv("SMOKE_TEST_K8S_TIMEOUT", "180"))

        try:
            k8s_config.load_incluster_config()
        except Exception:
            try:
                k8s_config.load_kube_config()
            except Exception as e:
                return SmokeTestResult(f"❌ Cannot load Kubernetes config: {e}")

        if not namespace:
            try:
                ns_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
                namespace = ns_path.read_text().strip()
            except Exception:
                namespace = "default"

        batch_v1 = client.BatchV1Api()
        core_v1 = client.CoreV1Api()

        job_name = f"smoke-test-{project_type}-{int(time.time()) % 100000}"

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=namespace,
                labels={"app.kubernetes.io/component": "smoke-test"},
            ),
            spec=client.V1JobSpec(
                backoff_limit=0,
                ttl_seconds_after_finished=60,
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        containers=[
                            client.V1Container(
                                name="smoke",
                                image=image,
                                command=["sh", "-c", command],
                                volume_mounts=[
                                    client.V1VolumeMount(
                                        name="workspace",
                                        mount_path="/app",
                                        read_only=True,
                                    )
                                ],
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="workspace",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                    claim_name=pvc_name,
                                    read_only=True,
                                ),
                            )
                        ],
                    )
                ),
            ),
        )

        try:
            batch_v1.create_namespaced_job(namespace=namespace, body=job)
            logger.info("Created smoke-test Job '%s' in namespace '%s'", job_name, namespace)
        except Exception as e:
            return SmokeTestResult(f"❌ Failed to create K8s Job: {e}")

        # Poll for completion
        succeeded = False
        elapsed = 0
        poll_interval = 3
        try:
            while elapsed < timeout:
                time.sleep(poll_interval)
                elapsed += poll_interval
                status = batch_v1.read_namespaced_job_status(job_name, namespace)
                if status.status.succeeded and status.status.succeeded >= 1:
                    succeeded = True
                    break
                if status.status.failed and status.status.failed >= 1:
                    break
        except Exception as e:
            return SmokeTestResult(f"❌ Error polling K8s Job status: {e}")

        # Retrieve logs
        logs = ""
        try:
            pods = core_v1.list_namespaced_pod(
                namespace,
                label_selector=f"job-name={job_name}",
            )
            if pods.items:
                logs = core_v1.read_namespaced_pod_log(
                    pods.items[0].metadata.name, namespace,
                )[:4000]
        except Exception:
            logs = "(could not retrieve logs)"

        # Cleanup
        try:
            batch_v1.delete_namespaced_job(
                job_name, namespace,
                body=client.V1DeleteOptions(propagation_policy="Background"),
            )
        except Exception:
            pass

        k8s_log = (
            f"job_name:  {job_name}\n"
            f"namespace: {namespace}\n"
            f"image:     {image}\n"
            f"elapsed:   {elapsed}s\n"
            f"succeeded: {succeeded}\n"
            f"─── pod log ───\n{logs or '(empty)'}"
        )

        if succeeded:
            return SmokeTestResult(f"✅ K8s Job smoke test passed ({image})", log=k8s_log)
        if elapsed >= timeout:
            return SmokeTestResult(f"❌ K8s Job smoke test timed out after {timeout}s:\n{logs}", log=k8s_log)
        return SmokeTestResult(f"❌ K8s Job smoke test failed ({image}):\n{logs}", log=k8s_log)


# ── Backend registry and runner ──────────────────────────────────────────────

_BACKENDS = {
    "syntax_only": SyntaxOnlyBackend,
    "podman": LocalContainerBackend,
    "docker": LocalContainerBackend,
    "k8s_job": KubernetesJobBackend,
}


def smoke_test_runner(
    project_type: str = "auto",
    workspace_path: Optional[str] = None,
) -> "SmokeTestResult":
    """Run a smoke test on the generated project to verify it compiles/loads.

    The execution backend is selected via the ``SMOKE_TEST_BACKEND``
    environment variable:

      - ``syntax_only`` (default) — static analysis only, safe everywhere
      - ``podman`` / ``docker`` — isolated container on the local host
      - ``k8s_job`` — Kubernetes Job (for OCP / cluster deployments)

    Args:
        project_type: Project type or "auto" to detect (default: "auto")

    Returns:
        SmokeTestResult with message and optional container log
    """
    try:
        workspace = Path(workspace_path).resolve() if workspace_path else _resolve_workspace()

        if project_type == "auto":
            project_type = _detect_project_type(workspace)
        if project_type == "unknown":
            return SmokeTestResult(f"❌ Could not detect project type in {workspace}")

        backend_name = os.getenv("SMOKE_TEST_BACKEND", "syntax_only")
        backend_cls = _BACKENDS.get(backend_name)
        if not backend_cls:
            return SmokeTestResult(
                f"❌ Unknown SMOKE_TEST_BACKEND '{backend_name}'. "
                f"Valid options: {', '.join(_BACKENDS)}"
            )

        backend = backend_cls()
        return backend.run(workspace, project_type)
    except Exception as e:
        return SmokeTestResult(f"❌ Smoke test error: {e}")


# Create FunctionTool instances
PytestRunnerTool = FunctionTool.from_defaults(
    fn=pytest_runner,
    name="pytest_runner",
    description="Run pytest tests in the workspace. Returns test results and coverage."
)

CodeCoverageTool = FunctionTool.from_defaults(
    fn=code_coverage,
    name="code_coverage",
    description="Run pytest with coverage analysis. Returns coverage percentage and report."
)

SmokeTestTool = FunctionTool.from_defaults(
    fn=smoke_test_runner,
    name="smoke_test_runner",
    description="Run a smoke test on the generated project to verify it compiles and loads correctly."
)


def create_workspace_test_tools(workspace_path: Path):
    """Pytest, coverage, and smoke-test tools bound to a specific workspace (thread-safe)."""
    from functools import partial

    ws = str(workspace_path)
    return [
        FunctionTool.from_defaults(
            fn=partial(pytest_runner, workspace_path=ws),
            name="pytest_runner",
            description="Run pytest tests in the workspace. Returns test results.",
        ),
        FunctionTool.from_defaults(
            fn=partial(code_coverage, workspace_path=ws),
            name="code_coverage",
            description="Run pytest with coverage analysis for a source directory (default src/).",
        ),
        FunctionTool.from_defaults(
            fn=partial(smoke_test_runner, workspace_path=ws),
            name="smoke_test_runner",
            description="Run a smoke / syntax validation pass on the project (backend from SMOKE_TEST_BACKEND env).",
        ),
    ]
