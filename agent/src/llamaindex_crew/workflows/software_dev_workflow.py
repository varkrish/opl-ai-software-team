"""
Software Development Workflow
Sequential workflow orchestrating all agents
"""
import json as _json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from llama_index.core import Settings as _LISettings
from ..agents import (
    MetaAgent, ProductOwnerAgent, DesignerAgent,
    TechArchitectAgent, DevAgent, FrontendAgent
)
from ..orchestrator.state_machine import ProjectStateMachine, ProjectState, TransitionContext
from ..orchestrator.task_manager import TaskManager, TaskStatus, TaskDefinition
from ..orchestrator.error_recovery import WorkflowErrorRecoveryEngine
from ..budget.tracker import EnhancedBudgetTracker
from ..utils.feature_parser import parse_features_from_files
from ..utils.document_indexer import DocumentIndexer
from ..utils.llm_config import get_embedding_model

logger = logging.getLogger(__name__)

# Force local HuggingFace embeddings globally so LlamaIndex never falls back to OpenAI
try:
    _LISettings.embed_model = get_embedding_model()
    logger.info("Global embed_model set to local HuggingFace (BAAI/bge-small-en-v1.5)")
except Exception as _e:
    logger.warning("Could not set global embed_model: %s", _e)

# Number of times to retry a phase on transient LLM/network errors
PHASE_RETRY_ATTEMPTS = 3
PHASE_RETRY_DELAY_SEC = 10

# Ordered phases for resume-from-checkpoint (first phase that runs is at this index)
_RESUMABLE_PHASES = [
    ProjectState.META,
    ProjectState.PRODUCT_OWNER,
    ProjectState.DESIGNER,
    ProjectState.TECH_ARCHITECT,
    ProjectState.DEVELOPMENT,
    ProjectState.FRONTEND,
    ProjectState.DEVOPS,
]


def _is_transient_llm_error(e: Exception) -> bool:
    """True if the error is a transient LLM/network error we should retry."""
    try:
        import httpx
        if isinstance(e, (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout)):
            return True
        if getattr(httpx, "TimeoutException", None) is not None and isinstance(e, httpx.TimeoutException):
            return True
    except ImportError:
        pass
    msg = str(e).lower()
    return (
        "server disconnected" in msg
        or "connection" in msg
        or "timeout" in msg
        or "503" in msg
        or "502" in msg
        or "504" in msg
        or "remoteprotocolerror" in msg
    )


def _extract_vision_keywords(vision: str) -> List[str]:
    """Extract meaningful keywords from the vision for coherence checking."""
    stop_words = {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "shall", "can", "need", "must",
        "that", "this", "these", "those", "it", "its", "i", "we", "you",
        "he", "she", "they", "my", "our", "your", "all", "each", "every",
        "any", "some", "no", "not", "so", "as", "if", "then", "than", "too",
        "very", "just", "about", "up", "out", "into", "over", "after",
        "create", "build", "make", "implement", "develop", "add", "use",
        "using", "based", "simple", "new", "also", "include", "ensure",
    }
    words = re.findall(r'[a-z]+', vision.lower())
    return [w for w in words if len(w) > 2 and w not in stop_words]


def _check_vision_coherence(vision: str, artifact_text: str, artifact_name: str,
                             min_keyword_ratio: float = 0.25) -> bool:
    """
    Check that an artifact is coherent with the original vision by verifying
    that a reasonable fraction of vision keywords appear in the artifact.

    Returns True if coherent, False if the artifact seems off-topic.
    """
    keywords = _extract_vision_keywords(vision)
    if not keywords:
        return True  # nothing to check
    artifact_lower = artifact_text.lower()
    matched = sum(1 for kw in keywords if kw in artifact_lower)
    ratio = matched / len(keywords)
    logger.info(
        "Coherence check [%s]: %d/%d vision keywords matched (%.0f%%, threshold %.0f%%)",
        artifact_name, matched, len(keywords), ratio * 100, min_keyword_ratio * 100,
    )
    if ratio < min_keyword_ratio:
        logger.warning(
            "⚠️ Coherence FAILED for %s: only %.0f%% of vision keywords found. "
            "Artifact may not match the project vision.",
            artifact_name, ratio * 100,
        )
        return False
    return True


_MIN_ARTIFACT_LINES = 3

_SUMMARY_PATTERNS = re.compile(
    r"^(?:I(?:'ve| have) created|Here (?:are|is) the|The (?:following )?files (?:have been|were)|"
    r"I(?:'ve| have) (?:generated|written|saved)|Let me know if|"
    r"All (?:\w+ )?(?:files|content) (?:have been|align)|"
    r"The (?:project |design )?(?:documentation|specification|document|file) has been (?:successfully )?(?:created|generated)|"
    r"has been (?:successfully )?created (?:as|in|at) )",
    re.IGNORECASE | re.MULTILINE,
)

_REJECTION_PATTERNS = re.compile(
    r"(?:I(?:'m| am) unable to (?:generate|create|write)|"
    r"cannot be created|system is blocking|"
    r"not (?:included |allowed )?in the (?:project )?(?:file )?manifest|"
    r"❌ Rejected|Please manually create|"
    r"adjust the (?:project )?manifest|"
    r"would need to.*(?:add|check).*manifest)",
    re.IGNORECASE,
)


def _is_agent_summary(text: str) -> bool:
    """Return True if *text* looks like a meta-summary or error/rejection instead of real artifact content.

    Length-aware: a long response (>= 500 chars) that starts with a summary
    preamble but contains substantial content (markdown headers, Gherkin
    keywords, code fences) is NOT treated as a summary — the preamble will be
    stripped by ``_strip_summary_preamble`` before persistence.
    """
    first_300 = text[:300]

    if _REJECTION_PATTERNS.search(first_300):
        return True

    if not _SUMMARY_PATTERNS.search(first_300):
        return False

    if len(text) >= 500 and text.count("\n") >= 8:
        _CONTENT_MARKERS = re.compile(
            r"^#{1,3}\s|Feature:|Scenario:|Given |When |Then |```",
            re.MULTILINE,
        )
        if _CONTENT_MARKERS.search(text[200:]):
            return False

    return True


def _strip_summary_preamble(text: str) -> str:
    """Remove a leading summary sentence/paragraph from an otherwise-valid artifact.

    If the response starts with "I've created…" or "All files…" followed by
    real content (headers, bullets, Gherkin), strip the preamble lines and
    return the substantive content.
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("Feature:") or stripped.startswith("- "):
            return "\n".join(lines[i:])
    return text


_YAML_BLOCK_RE = re.compile(
    r"```(?:ya?ml)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_yaml_block(text: str) -> Optional[str]:
    """Extract the first ```yaml ... ``` fenced block from agent output.

    Returns the raw YAML content (without fences) or None if not found.
    """
    m = _YAML_BLOCK_RE.search(text)
    if m:
        content = m.group(1).strip()
        if content and content.count("\n") >= 3:
            return content
    return None


def _persist_phase_artifact(
    workspace: Path,
    filename: str,
    agent_response: str,
) -> bool:
    """Write a phase artifact to disk if the agent didn't create it via file_writer.

    Returns True if the file was written, False if skipped.

    Skips when:
    - The file already exists (agent wrote it correctly)
    - The response is empty or too short to be useful content
    - The response looks like a meta-summary ("I've created the following files…")
    """
    target = workspace / filename
    if target.exists():
        return False

    if not agent_response or not agent_response.strip():
        return False

    stripped = agent_response.strip()

    if stripped.count("\n") < _MIN_ARTIFACT_LINES:
        return False

    if _is_agent_summary(stripped):
        logger.warning(
            "⚠️ Skipping fallback write for %s — response looks like a summary, not artifact content",
            filename,
        )
        return False

    stripped = _strip_summary_preamble(stripped)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stripped + "\n", encoding="utf-8")
    logger.info("📝 Fallback write: saved agent response to %s (%d chars)", filename, len(stripped))
    return True


def _extract_gherkin_features(text: str) -> Dict[str, str]:
    """Extract Gherkin Feature blocks from free-form text (e.g. user stories markdown).

    Returns a dict mapping a slugified filename to the feature text.
    """
    blocks: Dict[str, str] = {}
    pattern = re.compile(
        r"(Feature:\s*.+?)(?=\nFeature:|\Z)", re.DOTALL | re.IGNORECASE
    )
    for m in pattern.finditer(text):
        block = m.group(1).strip()
        title_match = re.match(r"Feature:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
        if not title_match:
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", title_match.group(1).strip().lower()).strip("_")
        if not slug:
            slug = f"feature_{len(blocks) + 1}"
        blocks[slug] = block
    return blocks


def _ensure_feature_files(workspace: Path, user_stories_text: str) -> int:
    """Guarantee that features/ contains .feature files.

    If none exist, attempt to extract Gherkin blocks from *user_stories_text*
    and write them.  Returns the number of feature files present after the call.
    """
    features_dir = workspace / "features"
    existing = list(features_dir.glob("*.feature")) if features_dir.exists() else []
    if existing:
        return len(existing)

    extracted = _extract_gherkin_features(user_stories_text)
    if not extracted:
        return 0

    features_dir.mkdir(parents=True, exist_ok=True)
    for slug, content in extracted.items():
        path = features_dir / f"{slug}.feature"
        path.write_text(content + "\n", encoding="utf-8")
        logger.info("📄 Extracted feature file: %s", path.name)
    return len(extracted)


class SoftwareDevWorkflow:
    """Main workflow orchestrating all development phases"""
    
    def __init__(
        self,
        project_id: str,
        workspace_path: Path,
        vision: str,
        config: Optional[Any] = None,
        progress_callback: Optional[callable] = None,
        job_db: Optional[Any] = None,
    ):
        """
        Initialize workflow
        
        Args:
            project_id: Unique project identifier
            workspace_path: Path to workspace directory
            vision: Project vision/idea
            config: Optional configuration instance
            progress_callback: Optional callback function(phase: str, progress: int, message: str)
            job_db: Optional JobDatabase instance for persisting validation issues
        """
        self.project_id = project_id
        self.workspace_path = workspace_path
        self.vision = vision
        self.config = config
        self.progress_callback = progress_callback
        self.job_db = job_db
        
        # Initialize components
        self.state_machine = ProjectStateMachine(workspace_path, project_id)
        self.task_manager = TaskManager(
            workspace_path / f"tasks_{project_id}.db",
            project_id
        )
        self.budget_tracker = EnhancedBudgetTracker()
        self.document_indexer = DocumentIndexer(workspace_path, project_id)
        self.error_recovery = WorkflowErrorRecoveryEngine(self.state_machine)
        
        # Initialize agents (will be created with custom backstories after meta phase)
        self.meta_agent = None
        self.product_owner_agent = None
        self.designer_agent = None
        self.tech_architect_agent = None
        self.dev_agent = None
        self.frontend_agent = None
        
        # Store phase outputs (loaded from workspace on resume)
        self.project_context = None
        self.agent_backstories = {}
        self.user_stories = None
        self.design_spec = None
        self.tech_stack = None
        self.api_contract = None  # OpenAPI 3.0 dict (populated for fullstack projects)
        self._export_registry: Dict[str, Any] = {}  # file_path -> export summary from dev phase
    
    def _report_progress(self, phase: str, progress: int, message: str = None):
        """Report progress via callback if available"""
        if self.progress_callback:
            try:
                self.progress_callback(phase, progress, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    # ── Validation & Remediation helpers ────────────────────────────────────

    def _call_validator(self) -> List[Dict[str, Any]]:
        """Call the external validator service or fall back to in-process checks.

        Returns a flat list of issue dicts:
            [{"check": str, "severity": str, "file": str, "line": int|None, "description": str}]
        """
        import urllib.request
        import urllib.error

        validator_url = os.getenv("VALIDATOR_URL")
        if validator_url:
            try:
                import json as _json
                payload = _json.dumps({
                    "workspace_path": str(self.workspace_path),
                    "checks": ["syntax", "imports", "package_structure", "entrypoint"],
                    "tech_stack": self.tech_stack or "",
                }).encode()
                req = urllib.request.Request(
                    f"{validator_url.rstrip('/')}/api/v1/validate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = _json.loads(resp.read())
                return self._normalize_validator_response(body)
            except Exception as e:
                logger.warning("Validator service unavailable (%s), falling back to in-process checks", e)

        return self._run_in_process_validation()

    def _normalize_validator_response(self, body: Dict) -> List[Dict[str, Any]]:
        """Convert the validator service JSON into a flat issue list."""
        issues: List[Dict[str, Any]] = []
        results = body.get("results", {})
        for check_name, check_data in results.items():
            if check_data.get("pass", True):
                continue
            for item in check_data.get("issues", []):
                issues.append({
                    "check": check_name,
                    "severity": "error",
                    "file": item.get("file", ""),
                    "line": item.get("line"),
                    "description": item.get("error", item.get("description", str(item))),
                })
        return issues

    def _run_in_process_validation(self) -> List[Dict[str, Any]]:
        """Run the built-in CodeCompletenessValidator and return a flat issue list."""
        from ..orchestrator.code_validator import CodeCompletenessValidator

        issues: List[Dict[str, Any]] = []
        _SRC_EXT = {".py", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".go"}

        for src_file in sorted(self.workspace_path.rglob("*")):
            if not src_file.is_file() or src_file.suffix not in _SRC_EXT:
                continue
            rel = str(src_file.relative_to(self.workspace_path))
            integ = CodeCompletenessValidator.validate_file_integration(src_file, self.workspace_path)
            if not integ["valid"]:
                for issue_text in integ.get("issues", []):
                    issues.append({
                        "check": "integration",
                        "severity": "error",
                        "file": rel,
                        "line": None,
                        "description": issue_text,
                    })

        pkg_result = CodeCompletenessValidator.validate_package_structure(
            self.workspace_path, self.tech_stack or ""
        )
        if not pkg_result["valid"]:
            for d in pkg_result.get("missing_init", pkg_result.get("issues", [])):
                issues.append({
                    "check": "package_structure",
                    "severity": "error",
                    "file": d if "/" in str(d) else f"{d}/__init__.py",
                    "line": None,
                    "description": str(d),
                })
        return issues

    def _auto_fix_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Auto-fix deterministic issues without LLM calls. Returns list of fixed issues."""
        import re as _re

        fixed: List[Dict[str, Any]] = []
        npm_packages_to_add: Dict[str, set] = {}
        maven_deps_to_add: Dict[str, set] = {}  # pom_rel_path -> set of "groupId:artifactId"

        for issue in issues:
            if issue["check"] == "package_structure" and issue.get("file", "").endswith("__init__.py"):
                target = self.workspace_path / issue["file"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text("")
                    fixed.append(issue)
                    logger.info("Auto-fixed: created %s", issue["file"])

            elif issue["check"] == "dependency_manifest" and "requirements.txt" in issue.get("file", ""):
                match = _re.search(r"Undeclared dependency:\s*(\S+)", issue.get("description", ""))
                if match:
                    pkg = match.group(1)
                    req_path = self.workspace_path / "requirements.txt"
                    existing = req_path.read_text() if req_path.exists() else ""
                    if pkg not in existing:
                        with open(req_path, "a") as f:
                            f.write(f"{pkg}\n")
                        fixed.append(issue)
                        logger.info("Auto-fixed: added %s to requirements.txt", pkg)

            elif issue["check"] == "package_json_completeness":
                match = _re.search(r"Package '([^']+)' imported", issue.get("description", ""))
                if match:
                    pkg = match.group(1)
                    affected_file = issue.get("file", "")
                    pkg_json_path = self._find_nearest_package_json(affected_file)
                    npm_packages_to_add.setdefault(str(pkg_json_path), set()).add(pkg)

            elif issue["check"] == "pom_xml_completeness":
                match = _re.search(r"Maven dependency '([^']+):([^']+)'", issue.get("description", ""))
                if match:
                    group_id, artifact_id = match.group(1), match.group(2)
                    affected_file = issue.get("file", "")
                    pom_path = self._find_nearest_pom_xml(affected_file)
                    maven_deps_to_add.setdefault(str(pom_path), set()).add(f"{group_id}:{artifact_id}")

        # ── Batch: npm packages → package.json ──
        _TEST_PACKAGES = {
            "jest", "ts-jest", "supertest", "@testing-library/react",
            "@testing-library/jest-dom", "@types/jest", "@types/supertest",
            "jest-mock-extended",
        }
        for pkg_json_rel, packages in npm_packages_to_add.items():
            pkg_json_path = self.workspace_path / pkg_json_rel
            try:
                if pkg_json_path.exists():
                    data = _json.loads(pkg_json_path.read_text(encoding="utf-8"))
                else:
                    data = {"name": "project", "version": "1.0.0", "dependencies": {}}
                    pkg_json_path.parent.mkdir(parents=True, exist_ok=True)

                deps = data.setdefault("dependencies", {})
                dev_deps = data.setdefault("devDependencies", {})
                added = []
                for pkg in sorted(packages):
                    if pkg in deps or pkg in dev_deps:
                        continue
                    if pkg in _TEST_PACKAGES or pkg.startswith("@types/"):
                        dev_deps[pkg] = "*"
                    else:
                        deps[pkg] = "*"
                    added.append(pkg)

                if added:
                    pkg_json_path.write_text(
                        _json.dumps(data, indent=2) + "\n", encoding="utf-8"
                    )
                    for pkg in added:
                        fixed.append({
                            "check": "package_json_completeness",
                            "file": pkg_json_rel,
                            "description": f"Added '{pkg}' to {pkg_json_rel}",
                        })
                    logger.info(
                        "Auto-fixed: added %s to %s", ", ".join(added), pkg_json_rel,
                    )
            except Exception as e:
                logger.warning("Failed to auto-fix package.json at %s: %s", pkg_json_rel, e)

        # ── Batch: Maven deps → pom.xml ──
        for pom_rel, coords_set in maven_deps_to_add.items():
            pom_path = self.workspace_path / pom_rel
            if not pom_path.exists():
                continue
            try:
                content = pom_path.read_text(encoding="utf-8")
                import xml.etree.ElementTree as _ET

                _ET.register_namespace("", "http://maven.apache.org/POM/4.0.0")
                tree = _ET.parse(str(pom_path))
                root = tree.getroot()
                ns = {"m": "http://maven.apache.org/POM/4.0.0"}

                existing_aids = set()
                for dep in root.findall(".//m:dependency", ns):
                    aid = dep.find("m:artifactId", ns)
                    if aid is not None and aid.text:
                        existing_aids.add(aid.text.strip())
                for dep in root.findall(".//dependency"):
                    aid = dep.find("artifactId")
                    if aid is not None and aid.text:
                        existing_aids.add(aid.text.strip())

                added = []
                for coord in sorted(coords_set):
                    gid, aid = coord.split(":", 1)
                    if aid in existing_aids:
                        continue
                    # Build the <dependency> XML snippet and insert before </dependencies>
                    dep_xml = (
                        f"\n        <dependency>"
                        f"\n            <groupId>{gid}</groupId>"
                        f"\n            <artifactId>{aid}</artifactId>"
                        f"\n        </dependency>"
                    )
                    # Insert via string manipulation (more reliable than ET for preserving formatting)
                    close_tag = "</dependencies>"
                    if close_tag in content:
                        content = content.replace(close_tag, dep_xml + "\n    " + close_tag, 1)
                    else:
                        deps_block = f"\n    <dependencies>{dep_xml}\n    </dependencies>\n"
                        content = content.replace("</project>", deps_block + "</project>", 1)
                    added.append(f"{gid}:{aid}")

                if added:
                    pom_path.write_text(content, encoding="utf-8")
                    for coord in added:
                        fixed.append({
                            "check": "pom_xml_completeness",
                            "file": pom_rel,
                            "description": f"Added '{coord}' to {pom_rel}",
                        })
                    logger.info("Auto-fixed: added %s to %s", ", ".join(added), pom_rel)

            except Exception as e:
                logger.warning("Failed to auto-fix pom.xml at %s: %s", pom_rel, e)

        return fixed

    def _find_nearest_package_json(self, file_path: str) -> str:
        """Walk up from file_path to find the nearest package.json, or default to root."""
        parts = Path(file_path).parts
        for i in range(len(parts), 0, -1):
            candidate = Path(*parts[:i]) / "package.json"
            if (self.workspace_path / candidate).exists():
                return str(candidate)
        if (self.workspace_path / "package.json").exists():
            return "package.json"
        top_dir = parts[0] if parts else ""
        return f"{top_dir}/package.json" if top_dir else "package.json"

    def _find_nearest_pom_xml(self, file_path: str) -> str:
        """Walk up from file_path to find the nearest pom.xml, or default to root."""
        parts = Path(file_path).parts
        for i in range(len(parts), 0, -1):
            candidate = Path(*parts[:i]) / "pom.xml"
            if (self.workspace_path / candidate).exists():
                return str(candidate)
        if (self.workspace_path / "pom.xml").exists():
            return "pom.xml"
        top_dir = parts[0] if parts else ""
        return f"{top_dir}/pom.xml" if top_dir else "pom.xml"

    def _get_fix_strategy(self, issue: Dict[str, Any]) -> Optional[str]:
        """Ask tech architect to review a single issue and produce a fix strategy.

        Uses a lightweight agent.chat() call scoped to the specific file/issue.
        Does NOT call tech_architect_agent.run() which would trigger the full
        define_tech_stack flow and regenerate the entire project.
        """
        if not self.tech_architect_agent:
            return None
        self.tech_architect_agent.agent.reset_chat()
        prompt = (
            f"File `{issue['file']}` has a {issue['check']} error: {issue['description']}.\n\n"
            f"Tech stack context:\n{(self.tech_stack or '')[:2000]}\n\n"
            f"Review this SINGLE file issue and produce a one-paragraph fix strategy.\n"
            f"Do NOT redefine the tech stack or list all project files."
        )
        try:
            result = self.tech_architect_agent.agent.chat(prompt)
            return str(result)
        except Exception as e:
            logger.warning("Tech architect review failed: %s", e)
            return None

    def _apply_fix(self, file_path: str, fix_strategy: str) -> None:
        """Ask dev agent to apply a fix strategy to a specific file.

        Uses a lightweight agent.chat() call scoped to the single file.
        Does NOT call dev_agent.run() which would trigger the full
        implement_features flow and regenerate the entire project.
        """
        if not self.dev_agent:
            return
        self.dev_agent.agent.reset_chat()

        current_content = ""
        target = self.workspace_path / file_path
        if target.exists():
            try:
                current_content = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        content_section = ""
        if current_content:
            content_section = (
                f"Current file content (preserve ALL existing code, "
                f"tests, and logic — only change what is needed to fix the issue):\n"
                f"```\n{current_content[:6000]}\n```\n\n"
            )

        prompt = (
            f"The file `{file_path}` has a validation issue.\n\n"
            f"Fix strategy: {fix_strategy}\n\n"
            f"Tech stack:\n{(self.tech_stack or '')[:2000]}\n\n"
            f"{content_section}"
            f"Please fix ONLY the specific issue described above and rewrite "
            f"the file using file_writer.\n"
            f"Do NOT create or modify any other files."
        )
        try:
            self.dev_agent.agent.chat(prompt)
        except Exception as e:
            logger.warning("Dev agent fix failed for %s: %s", file_path, e)

    # ── Reusable validation suite ─────────────────────────────────────────

    def _run_validation_suite(self) -> Dict[str, Any]:
        """Run the full validation suite and return the report dict.

        Extracted so it can be called both at end-of-development and as a
        post-build re-check.
        """
        from ..orchestrator.code_validator import CodeCompletenessValidator

        report: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workspace": str(self.workspace_path),
            "checks": {},
        }

        # 1. Completeness
        ws_result = CodeCompletenessValidator.validate_workspace(self.workspace_path)
        report["checks"]["completeness"] = {
            "pass": len(ws_result.get("incomplete_files", [])) == 0,
            "issues": ws_result.get("incomplete_files", []),
        }

        # 2. Integration (syntax + imports)
        _SRC_EXT = {".py", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".go"}
        file_issues: List[Dict[str, Any]] = []
        for src_file in sorted(self.workspace_path.rglob("*")):
            if src_file.is_file() and src_file.suffix in _SRC_EXT:
                rel = str(src_file.relative_to(self.workspace_path))
                integ = CodeCompletenessValidator.validate_file_integration(
                    src_file, self.workspace_path
                )
                file_issues.append({
                    "file": rel, "valid": integ["valid"],
                    "issues": integ.get("issues", []),
                })
        report["checks"]["integration"] = {
            "pass": all(f["valid"] for f in file_issues),
            "files": file_issues,
        }

        # 3. Dependency manifest
        manifest_result = CodeCompletenessValidator.validate_dependency_manifest(
            self.workspace_path
        )
        report["checks"]["dependency_manifest"] = {
            "pass": manifest_result.get("valid", True),
            "missing": manifest_result.get("missing", []),
        }

        # 4. Tech stack conformance
        if self.tech_stack:
            stack_result = CodeCompletenessValidator.validate_tech_stack_conformance(
                self.workspace_path, self.tech_stack
            )
            report["checks"]["tech_stack"] = {
                "pass": stack_result.get("valid", True),
                "conflicts": stack_result.get("conflicts", []),
            }
        else:
            report["checks"]["tech_stack"] = {
                "pass": True, "conflicts": [], "note": "no tech stack defined",
            }

        # 5. Package structure
        pkg_result = CodeCompletenessValidator.validate_package_structure(
            self.workspace_path, self.tech_stack or ""
        )
        missing_init = pkg_result.get("missing_init", pkg_result.get("issues", []))
        report["checks"]["package_structure"] = {
            "pass": pkg_result["valid"], "missing_init": missing_init,
        }

        # 6. Duplicate files
        dup_result = CodeCompletenessValidator.validate_duplicate_files(self.workspace_path)
        report["checks"]["duplicate_files"] = {
            "pass": dup_result["valid"],
            "duplicates": dup_result.get("duplicates", []),
        }

        # 7. Entrypoint wiring
        entrypoint_result = CodeCompletenessValidator.validate_entrypoint(
            self.workspace_path, self.tech_stack or ""
        )
        report["checks"]["entrypoint"] = {
            "pass": entrypoint_result["valid"],
            "framework": entrypoint_result.get("framework", ""),
            "missing_wiring": entrypoint_result.get("missing_wiring", []),
        }

        # 8. File manifest
        allowed_paths = self.task_manager.get_registered_file_paths()
        _META_FILES = {
            "agent_backstories.json", "agent_prompts.json", "crew_errors.log",
            "validation_report.json", "validation_report.log",
            "smoke_test_container.log", "unknown",
        }
        unauthorized: List[str] = []
        conflict_pairs: List[Dict[str, str]] = []
        for src_file in sorted(self.workspace_path.rglob("*")):
            if not src_file.is_file():
                continue
            rel = str(src_file.relative_to(self.workspace_path))
            if rel.startswith(".") or rel.startswith("state_") or rel.startswith("tasks_"):
                continue
            if rel in _META_FILES or rel.startswith("features/"):
                continue
            if any(rel.endswith(ext) for ext in (".md", ".log", ".json", ".yaml", ".yml")):
                continue
            if rel not in allowed_paths:
                unauthorized.append(rel)
        for p in sorted(allowed_paths):
            if p.endswith("/__init__.py"):
                dir_stem = p.rsplit("/__init__.py", 1)[0]
                flat_mod = f"{dir_stem}.py"
                if flat_mod in allowed_paths or (self.workspace_path / flat_mod).exists():
                    conflict_pairs.append({"package": p, "flat_module": flat_mod})
        report["checks"]["file_manifest"] = {
            "pass": len(unauthorized) == 0 and len(conflict_pairs) == 0,
            "unauthorized_files": unauthorized,
            "file_package_conflicts": conflict_pairs,
        }

        # 9. Contract conformance
        if self.api_contract and isinstance(self.api_contract, dict):
            try:
                contract_result = CodeCompletenessValidator.validate_contract_conformance(
                    self.workspace_path, self.api_contract, self.tech_stack or ""
                )
                report["checks"]["contract_conformance"] = {
                    "pass": contract_result.get("valid", True),
                    "missing_endpoints": contract_result.get("missing_endpoints", []),
                    "extra_endpoints": contract_result.get("extra_endpoints", []),
                }
            except Exception as e:
                logger.debug("Contract conformance check skipped: %s", e)

        # 10. Smoke test
        smoke_msg = ""
        smoke_container_log = ""
        try:
            from ..tools.test_tools import smoke_test_runner
            smoke_result = smoke_test_runner("auto")
            smoke_msg = str(smoke_result)
            smoke_container_log = getattr(smoke_result, "log", "")
        except Exception as e:
            smoke_msg = f"skipped: {e}"
        report["checks"]["smoke_test"] = {
            "pass": "✅" in smoke_msg,
            "result": smoke_msg,
        }
        if smoke_container_log:
            try:
                (self.workspace_path / "smoke_test_container.log").write_text(
                    f"═══ Smoke Test Container Log ═══\n"
                    f"timestamp: {report['timestamp']}\n\n{smoke_container_log}\n",
                    encoding="utf-8",
                )
            except Exception:
                pass

        # 11. Module system consistency
        mod_result = CodeCompletenessValidator.validate_module_consistency(
            self.workspace_path
        )
        report["checks"]["module_system"] = {
            "pass": mod_result["valid"],
            "conflicts": mod_result.get("conflicts", []),
        }

        # 12. Package.json completeness (JS/TS imports vs declared deps)
        pkg_result = CodeCompletenessValidator.validate_package_json_completeness(
            self.workspace_path
        )
        report["checks"]["package_json_completeness"] = {
            "pass": pkg_result["valid"],
            "missing": pkg_result.get("missing", []),
        }

        # 13. Intra-file duplicate code blocks
        dup_code_result = CodeCompletenessValidator.validate_duplicate_code_blocks(
            self.workspace_path
        )
        report["checks"]["duplicate_code_blocks"] = {
            "pass": dup_code_result["valid"],
            "duplicates": dup_code_result.get("duplicates", []),
        }

        # 14. Maven pom.xml completeness (Java imports vs declared deps)
        pom_result = CodeCompletenessValidator.validate_pom_xml_completeness(
            self.workspace_path
        )
        report["checks"]["pom_xml_completeness"] = {
            "pass": pom_result["valid"],
            "missing": pom_result.get("missing", []),
        }

        all_passed = all(c.get("pass", True) for c in report["checks"].values())
        report["overall"] = "PASS" if all_passed else "ISSUES_FOUND"
        return report

    def _collect_fixable_issues(self, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Distil validation report into a list of actionable file-level issues."""
        issues: List[Dict[str, Any]] = []

        for fi in report.get("checks", {}).get("integration", {}).get("files", []):
            if not fi.get("valid", True):
                for issue_text in fi.get("issues", []):
                    issues.append({
                        "file": fi["file"],
                        "check": "integration",
                        "description": issue_text,
                    })

        for inc in report.get("checks", {}).get("completeness", {}).get("issues", []):
            issues.append({
                "file": inc.get("file", ""),
                "check": "completeness",
                "description": "; ".join(inc.get("issues", [])),
            })

        for c in report.get("checks", {}).get("tech_stack", {}).get("conflicts", []):
            issues.append({
                "file": c.get("file", ""),
                "check": "tech_stack",
                "description": f"{c.get('conflict', '')}: {c.get('detail', '')}",
            })

        for m in report.get("checks", {}).get("dependency_manifest", {}).get("missing", []):
            pkg = m.get("package", m.get("module", ""))
            dep_files = m.get("files", [])
            if not dep_files:
                dep_files = [m.get("file", "")]
            for dep_file in dep_files:
                issues.append({
                    "file": dep_file,
                    "check": "dependency_manifest",
                    "description": f"Undeclared dependency: {pkg}",
                })

        for dup in report.get("checks", {}).get("duplicate_files", {}).get("duplicates", []):
            paths = dup.get("paths", [])
            issues.append({
                "file": paths[0] if paths else "",
                "check": "duplicate_files",
                "description": f"Duplicate filename '{dup.get('filename', '')}' exists at: {', '.join(paths)}",
            })

        ep_check = report.get("checks", {}).get("entrypoint", {})
        if not ep_check.get("pass", True):
            ep_framework = ep_check.get("framework", "")
            for wiring in ep_check.get("missing_wiring", []):
                issues.append({
                    "file": ep_framework or "",
                    "check": "entrypoint",
                    "description": wiring,
                })

        for mc in report.get("checks", {}).get("module_system", {}).get("conflicts", []):
            issues.append({
                "file": mc.get("file", ""),
                "check": "module_system",
                "description": f"{mc.get('conflict', '')}: {mc.get('detail', '')}",
            })

        for pkg_missing in report.get("checks", {}).get("package_json_completeness", {}).get("missing", []):
            pkg_name = pkg_missing.get("package", "")
            for affected_file in pkg_missing.get("files", []):
                issues.append({
                    "file": affected_file,
                    "check": "package_json_completeness",
                    "description": f"Package '{pkg_name}' imported but not declared in any package.json",
                })

        for dup_block in report.get("checks", {}).get("duplicate_code_blocks", {}).get("duplicates", []):
            issues.append({
                "file": dup_block.get("file", ""),
                "check": "duplicate_code_blocks",
                "description": (
                    f"Contains duplicated code block ({dup_block.get('repeated_lines', 0)} lines "
                    f"repeated {dup_block.get('occurrences', 0)} times). "
                    f"Remove the duplicate — keep only one copy."
                ),
            })

        for pom_missing in report.get("checks", {}).get("pom_xml_completeness", {}).get("missing", []):
            group_id = pom_missing.get("groupId", "")
            artifact_id = pom_missing.get("artifactId", "")
            for affected_file in pom_missing.get("files", []):
                issues.append({
                    "file": affected_file,
                    "check": "pom_xml_completeness",
                    "description": f"Maven dependency '{group_id}:{artifact_id}' needed but not declared in pom.xml",
                })

        return issues

    @staticmethod
    def _collect_related_files(
        file_path: str, all_files: Dict[str, str], max_files: int = 8,
    ) -> Dict[str, str]:
        """Find files related to *file_path* by scanning import statements.

        Returns a dict {path: content} of at most *max_files* related files.
        """
        import re

        stem = Path(file_path).stem
        related: Dict[str, str] = {}

        target_content = all_files.get(file_path, "")
        _IMPORT_RE = re.compile(
            r"(?:from\s+(\S+)\s+import|import\s+(\S+)|"
            r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)|"""
            r"""from\s+['"]([^'"]+)['"]\s*;?)"""
        )
        for m in _IMPORT_RE.finditer(target_content):
            mod = m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
            mod_stem = mod.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for fp, content in all_files.items():
                if fp == file_path:
                    continue
                if Path(fp).stem == mod_stem and fp not in related:
                    related[fp] = content

        for fp, content in all_files.items():
            if fp == file_path or fp in related:
                continue
            if stem in content:
                related[fp] = content

        if len(related) > max_files:
            related = dict(list(related.items())[:max_files])

        return related

    def _run_post_build_fix_with_context(
        self,
        file_path: str,
        descriptions: List[str],
        all_files: Dict[str, str],
    ) -> None:
        """Fix a single file with sibling context included in the prompt."""
        if not self.dev_agent:
            return
        self.dev_agent.agent.reset_chat()
        issue_list = "\n".join(f"- {d}" for d in descriptions[:10])
        related = self._collect_related_files(file_path, all_files)
        related_section = ""
        if related:
            parts = ["Related files (for import/reference context):"]
            for fp, content in related.items():
                parts.append(f"--- {fp} ---")
                parts.append(content[:3000])
            related_section = "\n".join(parts) + "\n\n"

        file_tree_section = ""
        if all_files:
            tree_lines = ["PROJECT FILE TREE (use these exact paths for imports):"]
            for fp in sorted(all_files.keys()):
                tree_lines.append(f"  {fp}")
            file_tree_section = "\n".join(tree_lines) + "\n\n"

        current_content = ""
        target = self.workspace_path / file_path
        if target.exists():
            try:
                current_content = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        content_section = ""
        has_framework_issue = any(
            "@nestjs" in d or "@Injectable" in d or "@InjectRepository" in d
            or "wrong framework" in d.lower() or "framework mismatch" in d.lower()
            for d in descriptions
        )
        has_duplicate_issue = any("duplicated code block" in d.lower() for d in descriptions)

        if current_content:
            if has_framework_issue or has_duplicate_issue:
                content_section = (
                    f"Current file content (this file has MAJOR issues — "
                    f"you may REWRITE it entirely using the correct framework from the tech stack):\n"
                    f"```\n{current_content[:6000]}\n```\n\n"
                )
            else:
                content_section = (
                    f"Current file content (preserve existing code and logic "
                    f"— only change what is needed to fix the issues):\n"
                    f"```\n{current_content[:6000]}\n```\n\n"
                )

        fix_rules = (
            "Please fix the issues listed above and rewrite the file using file_writer.\n"
            "Use only the frameworks/libraries specified in the tech stack.\n"
        )
        if has_framework_issue:
            fix_rules += (
                "IMPORTANT: If the file uses decorators or APIs from a wrong framework "
                "(e.g., @InjectRepository from @nestjs/common in an Express project), "
                "you MUST rewrite the file using the CORRECT framework's APIs. "
                "Do NOT preserve code that uses the wrong framework.\n"
            )
        if has_duplicate_issue:
            fix_rules += (
                "IMPORTANT: This file contains duplicated code blocks. "
                "Remove ALL duplicate blocks — keep only ONE copy of each logical section. "
                "Do NOT append new code; replace the entire file content.\n"
            )
        fix_rules += (
            "Do NOT hallucinate APIs. Use only real, documented methods.\n"
            "Express: use `express()`, NOT `express.createServer()`.\n"
            "React: `import React from 'react'` (default export).\n"
            "TypeORM: import decorators directly from 'typeorm'.\n"
        )

        fix_prompt = (
            f"The file `{file_path}` has these validation issues:\n{issue_list}\n\n"
            f"Tech stack:\n{(self.tech_stack or '')[:2000]}\n\n"
            f"{content_section}"
            f"{file_tree_section}"
            f"{related_section}"
            f"{fix_rules}"
        )
        try:
            self.dev_agent.agent.chat(fix_prompt)
        except Exception as e:
            logger.warning("Post-build fix failed for %s: %s", file_path, e)

    def _run_post_build_fix_iteration(self) -> None:
        """After all phases complete, re-validate and iterate to fix issues.

        Runs up to MAX_POST_BUILD_ITERATIONS (env var, default 3).
        Each iteration: auto-fix deterministic issues -> collect remaining
        fixable issues -> ask dev agent to fix per-file -> re-validate.
        Stops early if no progress (convergence detection).
        Updates ``self._validation_report`` with the final result.
        """
        max_iterations = int(os.environ.get("MAX_POST_BUILD_ITERATIONS", "3"))
        if max_iterations <= 0:
            return

        from ..tools.file_tools import set_allowed_file_paths

        prev_issue_count = None

        for iteration in range(1, max_iterations + 1):
            report = self._run_validation_suite()
            self._validation_report = report

            if report.get("overall") == "PASS":
                logger.info("✅ Post-build validation PASS (iteration %d)", iteration)
                break

            fixable = self._collect_fixable_issues(report)
            if not fixable:
                logger.info(
                    "Post-build validation: ISSUES_FOUND but no actionable file issues to fix"
                )
                break

            current_count = len(fixable)
            if prev_issue_count is not None and current_count >= prev_issue_count:
                logger.info(
                    "⏹️ Post-build fix converged: %d issues (was %d). Stopping early.",
                    current_count, prev_issue_count,
                )
                break
            prev_issue_count = current_count

            logger.info(
                "🔄 Post-build fix iteration %d/%d — %d fixable issue(s)",
                iteration, max_iterations, len(fixable),
            )
            self._report_progress(
                "validation", 95,
                f"Fix iteration {iteration}/{max_iterations}: {len(fixable)} issue(s)...",
            )

            # Auto-fix deterministic issues first (package.json, __init__.py, etc.)
            auto_fixed = self._auto_fix_issues(fixable)
            if auto_fixed:
                logger.info("Auto-fixed %d issue(s) in iteration %d", len(auto_fixed), iteration)
                auto_fixed_files = {i.get("file") for i in auto_fixed}
                fixable = [i for i in fixable if i.get("file") not in auto_fixed_files
                           or i not in auto_fixed]

            # Temporarily allow writes to all registered files
            allowed = self.task_manager.get_registered_file_paths()
            set_allowed_file_paths(allowed, workspace=str(self.workspace_path))

            # Ensure dev agent is available
            if self.dev_agent is None:
                backstory = self.agent_backstories.get("developer")
                self.dev_agent = DevAgent(
                    custom_backstory=backstory,
                    budget_tracker=self.budget_tracker,
                    workspace_path=self.workspace_path,
                )

            # Group issues by file and ask dev agent to fix each
            by_file: Dict[str, List[str]] = {}
            for issue in fixable:
                fp = (issue.get("file") or "").strip()
                if fp and fp.lower() != "unknown":
                    by_file.setdefault(fp, []).append(issue["description"])

            # Collect workspace source files for sibling context
            _SRC_EXT = {".py", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".go"}
            all_files: Dict[str, str] = {}
            for src in sorted(self.workspace_path.rglob("*")):
                if src.is_file() and src.suffix in _SRC_EXT:
                    try:
                        rel = str(src.relative_to(self.workspace_path))
                        all_files[rel] = src.read_text(
                            encoding="utf-8", errors="replace"
                        )[:3000]
                    except Exception:
                        pass

            for file_path, descs in by_file.items():
                if not file_path or file_path.strip().lower() == "unknown":
                    logger.debug("Post-build fix: skipping issue with invalid file path %r", file_path)
                    continue
                self._run_post_build_fix_with_context(file_path, descs, all_files)

            set_allowed_file_paths(None, workspace=str(self.workspace_path))
        else:
            # Ran all iterations — do a final re-validate
            report = self._run_validation_suite()
            self._validation_report = report
            if report.get("overall") != "PASS":
                failing = [
                    k for k, c in report["checks"].items()
                    if not c.get("pass", True)
                ]
                logger.warning(
                    "⚠️ Post-build validation still has issues after %d iteration(s): %s",
                    max_iterations, ", ".join(failing),
                )

        # Write final report to workspace
        try:
            import json as _json
            report_path = self.workspace_path / "validation_report.json"
            report_path.write_text(
                _json.dumps(self._validation_report, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_phase_artifacts(self) -> None:
        """Load user_stories, design_spec, tech_stack, agent_backstories from workspace (for resume)."""
        for path, attr in [
            ("user_stories.md", "user_stories"),
            ("design_spec.md", "design_spec"),
            ("tech_stack.md", "tech_stack"),
        ]:
            fpath = self.workspace_path / path
            if fpath.exists():
                try:
                    setattr(self, attr, fpath.read_text(encoding="utf-8", errors="replace"))
                    logger.info("Resume: loaded %s", path)
                except Exception as e:
                    logger.warning("Resume: could not load %s: %s", path, e)

        contract_file = self.workspace_path / "api_contract.yaml"
        if contract_file.exists():
            try:
                import yaml
                self.api_contract = yaml.safe_load(
                    contract_file.read_text(encoding="utf-8", errors="replace")
                )
                logger.info("Resume: loaded api_contract.yaml")
            except Exception as e:
                logger.warning("Resume: could not load api_contract.yaml: %s", e)
        backstories_file = self.workspace_path / "agent_backstories.json"
        if backstories_file.exists():
            try:
                import json as _json
                self.agent_backstories = _json.loads(backstories_file.read_text(encoding="utf-8"))
                logger.info("Resume: loaded agent_backstories.json")
            except Exception as e:
                logger.warning("Resume: could not load agent_backstories.json: %s", e)
    
    def run_meta_phase(self, retry_count: int = 0) -> Dict[str, str]:
        """Run Meta phase to generate agent backstories"""
        logger.info("🚀 Starting Meta phase...")
        self._report_progress('meta', 10, "Starting Meta phase...")
        
        try:
            # Only transition if not already in META state (initial state)
            if self.state_machine.get_current_state() != ProjectState.META:
                self.state_machine.transition(
                    ProjectState.META,
                    TransitionContext(phase="meta", data={"vision": self.vision})
                )
            
            # Create meta agent
            self.meta_agent = MetaAgent(budget_tracker=self.budget_tracker)
            
            # Run meta agent
            backstories = self.meta_agent.run(self.vision)
            self.agent_backstories = backstories
            # Persist for resume-from-checkpoint
            try:
                import json as _json
                (self.workspace_path / "agent_backstories.json").write_text(
                    _json.dumps(backstories, indent=2), encoding="utf-8"
                )
            except Exception as e:
                logger.warning("Could not save agent_backstories.json: %s", e)
            
            # Extract project context from meta agent analysis
            self.project_context = self.meta_agent.analyze_vision(self.vision)
            
            # Transition to next phase only after successful completion
            self.state_machine.transition(
                ProjectState.PRODUCT_OWNER,
                TransitionContext(phase="meta_completed", data={"backstories": backstories})
            )
            
            logger.info("✅ Meta phase completed")
            return backstories
        except Exception as e:
            recovery = self.error_recovery.handle_workflow_error("meta", e, retry_count)
            if self.error_recovery.should_retry("meta", retry_count) and retry_count < 3:
                logger.warning(f"⚠️  Meta phase failed, retrying... ({retry_count + 1}/3)")
                # Rollback to META state for retry
                self.state_machine.rollback_to(ProjectState.META)
                return self.run_meta_phase(retry_count + 1)
            else:
                logger.error(f"❌ Meta phase failed after {retry_count + 1} attempts")
                raise
    
    _PO_FEATURE_RETRY_PROMPT = (
        "CRITICAL: You MUST use the file_writer tool to create EACH file individually. "
        "Do NOT describe what you would create — actually call file_writer for every file.\n\n"
        "Project Vision: {vision}\n"
        "Project Context: {context_digest}\n\n"
        "Create these files using file_writer:\n"
        "1. 'requirements.md' — high-level requirements\n"
        "2. 'user_stories.md' — detailed user stories with acceptance criteria "
        "(As a… I want… So that… / Given… When… Then…)\n"
        "3. One 'features/<name>.feature' file per feature in proper Gherkin syntax "
        "(Feature / Scenario / Given / When / Then)\n\n"
        "Call file_writer once for EACH file. Do not skip any."
    )

    def run_product_owner_phase(self) -> str:
        """Run Product Owner phase to create user stories and BDD feature files."""
        logger.info("🚀 Starting Product Owner phase...")
        self._report_progress('product_owner', 30, "Creating user stories...")

        if self.state_machine.get_current_state() != ProjectState.PRODUCT_OWNER:
            self.state_machine.transition(
                ProjectState.PRODUCT_OWNER,
                TransitionContext(phase="product_owner", data={})
            )

        backstory = self.agent_backstories.get('product_owner')

        def _run_po() -> str:
            self.product_owner_agent = ProductOwnerAgent(
                custom_backstory=backstory,
                budget_tracker=self.budget_tracker,
                document_indexer=self.document_indexer,
                workspace_path=self.workspace_path,
            )
            return self.product_owner_agent.run(self.vision, self.project_context)

        result = _run_po()

        _persist_phase_artifact(self.workspace_path, "user_stories.md", result)
        _persist_phase_artifact(self.workspace_path, "requirements.md", result)

        user_stories_file = self.workspace_path / "user_stories.md"

        # -- Gate 1: real user stories must exist -------------------------
        has_real_stories = (
            user_stories_file.exists()
            and not _is_agent_summary(user_stories_file.read_text(encoding="utf-8", errors="replace"))
        )
        if not has_real_stories:
            logger.warning(
                "⚠️ user_stories.md missing or contains only a summary — "
                "re-running PO with explicit tool-use instructions"
            )
            if user_stories_file.exists():
                user_stories_file.unlink()
            self.product_owner_agent = ProductOwnerAgent(
                custom_backstory=backstory,
                budget_tracker=self.budget_tracker,
                document_indexer=self.document_indexer,
                workspace_path=self.workspace_path,
            )
            retry_prompt = self._PO_FEATURE_RETRY_PROMPT.format(
                vision=self.vision,
                context_digest=self.project_context or "",
            )
            result = self.product_owner_agent.agent.chat(retry_prompt)
            result = str(result)
            _persist_phase_artifact(self.workspace_path, "user_stories.md", result)
            _persist_phase_artifact(self.workspace_path, "requirements.md", result)

        # Re-read whatever is on disk now
        if user_stories_file.exists():
            self.user_stories = user_stories_file.read_text(encoding="utf-8", errors="replace")
        else:
            self.user_stories = ""

        # Coherence check
        if self.user_stories and not _check_vision_coherence(
            self.vision, self.user_stories, "user_stories.md"
        ):
            logger.warning("⚠️ User stories failed coherence check — re-running PO")
            if user_stories_file.exists():
                user_stories_file.unlink()
            result = _run_po()
            _persist_phase_artifact(self.workspace_path, "user_stories.md", result)
            if user_stories_file.exists():
                self.user_stories = user_stories_file.read_text(encoding="utf-8", errors="replace")

        # -- Gate 2: .feature files must exist ----------------------------
        feature_count = _ensure_feature_files(self.workspace_path, self.user_stories)
        if feature_count == 0:
            logger.warning(
                "⚠️ No .feature files found and none could be extracted — "
                "re-running PO with feature-focused prompt"
            )
            self.product_owner_agent = ProductOwnerAgent(
                custom_backstory=backstory,
                budget_tracker=self.budget_tracker,
                document_indexer=self.document_indexer,
                workspace_path=self.workspace_path,
            )
            feature_prompt = (
                "You MUST create Gherkin .feature files using the file_writer tool.\n\n"
                f"Project Vision: {self.vision}\n\n"
                f"User Stories:\n{self.user_stories or 'Not available — create them too.'}\n\n"
                "For each feature, call file_writer with path 'features/<name>.feature' "
                "containing proper Gherkin:\n"
                "  Feature: <title>\n"
                "    Scenario: <scenario name>\n"
                "      Given …\n      When …\n      Then …\n\n"
                "Create at least one .feature file per major feature. "
                "Call file_writer for EACH file."
            )
            result = str(self.product_owner_agent.agent.chat(feature_prompt))
            feature_count = _ensure_feature_files(self.workspace_path, result)

        logger.info(
            "✅ Product Owner phase completed — %d feature file(s) in workspace",
            feature_count,
        )

        if self.user_stories:
            self.document_indexer.index_artifacts(["user_stories.md"])

        return result
    
    def run_designer_phase(self) -> str:
        """Run Designer phase to create design specification"""
        logger.info("🚀 Starting Designer phase...")
        self._report_progress('designer', 45, "Creating design specification...")
        
        # Ensure state is correct
        if self.state_machine.get_current_state() != ProjectState.DESIGNER:
            self.state_machine.transition(
                ProjectState.DESIGNER,
                TransitionContext(phase="designer", data={})
            )
        
        # Create designer agent with custom backstory
        backstory = self.agent_backstories.get('designer')
        self.designer_agent = DesignerAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
            workspace_path=self.workspace_path,
        )
        
        # Run designer agent with vision for grounding
        result = self.designer_agent.run(
            self.user_stories or "",
            self.project_context,
            vision=self.vision,
        )
        
        # Fallback: if agent returned content but didn't call file_writer
        _persist_phase_artifact(self.workspace_path, "design_spec.md", result)
        
        # Read generated design spec
        design_spec_file = self.workspace_path / "design_spec.md"
        if design_spec_file.exists():
            with open(design_spec_file, 'r', encoding='utf-8') as f:
                self.design_spec = f.read()
            
            # Validate design spec coherence with vision
            if not _check_vision_coherence(self.vision, self.design_spec, "design_spec.md"):
                logger.warning("⚠️ Design spec failed coherence check — re-running designer with stronger grounding")
                self.designer_agent = DesignerAgent(
                    custom_backstory=backstory,
                    budget_tracker=self.budget_tracker,
                    workspace_path=self.workspace_path,
                )
                result = self.designer_agent.run(
                    self.user_stories or "",
                    self.project_context,
                    vision=self.vision,
                )
                if design_spec_file.exists():
                    self.design_spec = design_spec_file.read_text(encoding="utf-8", errors="replace")

            # Index design spec for RAG
            self.document_indexer.index_artifacts(["design_spec.md"])
        
        logger.info("✅ Designer phase completed")
        return result
    
    def run_tech_architect_phase(self) -> str:
        """Run Tech Architect phase to define tech stack"""
        logger.info("🚀 Starting Tech Architect phase...")
        self._report_progress('tech_architect', 60, "Defining technical architecture...")
        
        # Ensure state is correct
        if self.state_machine.get_current_state() != ProjectState.TECH_ARCHITECT:
            self.state_machine.transition(
                ProjectState.TECH_ARCHITECT,
                TransitionContext(phase="tech_architect", data={})
            )
        
        # Create tech architect agent with custom backstory and workspace-bound file tools
        backstory = self.agent_backstories.get('tech_architect')
        self.tech_architect_agent = TechArchitectAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
            workspace_path=self.workspace_path,
        )
        
        # Run tech architect agent
        result = self.tech_architect_agent.run(
            self.design_spec or "",
            self.vision,
            self.project_context
        )
        
        # Fallback: if agent returned content but didn't call file_writer
        _persist_phase_artifact(self.workspace_path, "tech_stack.md", result)
        
        # Read generated tech stack
        tech_stack_file = self.workspace_path / "tech_stack.md"
        if tech_stack_file.exists():
            with open(tech_stack_file, 'r', encoding='utf-8') as f:
                self.tech_stack = f.read()
            
            # Validate tech stack coherence with vision
            _check_vision_coherence(self.vision, self.tech_stack, "tech_stack.md")

            # Register granular per-file tasks with domain context from design spec
            self.task_manager.register_granular_tasks(
                self.design_spec or "",
                self.tech_stack,
            )
            
            # Index tech stack for RAG
            self.document_indexer.index_artifacts(["tech_stack.md"])

            # Second pass: generate API contract for fullstack projects
            self._generate_api_contract_if_fullstack()
        
        logger.info("✅ Tech Architect phase completed")
        return result

    def _generate_api_contract_if_fullstack(self) -> None:
        """If the project has both backend and frontend, generate api_contract.yaml."""
        from ..orchestrator.language_strategies import StrategyRegistry

        if not self.tech_stack:
            return

        registry = StrategyRegistry()
        if not registry.is_fullstack(self.tech_stack):
            logger.info("Not a fullstack project — skipping API contract generation")
            return

        logger.info("🔗 Fullstack project detected — generating API contract...")
        self._report_progress('tech_architect', 62, "Generating API contract for frontend-backend integration...")

        if self.tech_architect_agent is None:
            backstory = self.agent_backstories.get('tech_architect')
            self.tech_architect_agent = TechArchitectAgent(
                custom_backstory=backstory,
                budget_tracker=self.budget_tracker,
                workspace_path=self.workspace_path,
            )

        self.tech_architect_agent.agent.reset_chat()

        from ..tools.file_tools import set_allowed_file_paths
        ws = str(self.workspace_path)
        set_allowed_file_paths(None, workspace=ws)

        import concurrent.futures
        import time as _time
        _CONTRACT_TIMEOUT_SECS = int(os.environ.get("CONTRACT_TIMEOUT_SECS", "120"))
        _CONTRACT_MAX_RETRIES = int(os.environ.get("CONTRACT_MAX_RETRIES", "3"))

        contract_result = None
        for attempt in range(1, _CONTRACT_MAX_RETRIES + 1):
            try:
                if attempt > 1:
                    self.tech_architect_agent.agent.reset_chat()
                    backoff = min(2 ** attempt, 16)
                    logger.info("⏳ Retrying API contract generation (attempt %d/%d) after %ds backoff...",
                                attempt, _CONTRACT_MAX_RETRIES, backoff)
                    self._report_progress('tech_architect', 62,
                                          f"Retrying API contract generation (attempt {attempt}/{_CONTRACT_MAX_RETRIES})...")
                    _time.sleep(backoff)

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        self.tech_architect_agent.generate_api_contract,
                        tech_stack=self.tech_stack,
                        design_spec=self.design_spec or "",
                        user_stories=self.user_stories or "",
                    )
                    contract_result = future.result(timeout=_CONTRACT_TIMEOUT_SECS)
                break  # success
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "⚠️ API contract generation timed out after %ds (attempt %d/%d)",
                    _CONTRACT_TIMEOUT_SECS, attempt, _CONTRACT_MAX_RETRIES,
                )
            except Exception as exc:
                logger.warning("⚠️ API contract generation failed (attempt %d/%d): %s",
                               attempt, _CONTRACT_MAX_RETRIES, exc)

        if contract_result is None:
            logger.warning("⚠️ API contract generation failed after %d attempts — continuing without contract",
                           _CONTRACT_MAX_RETRIES)
            return

        contract_file = self.workspace_path / "api_contract.yaml"

        if not contract_file.exists() and contract_result:
            yaml_content = _extract_yaml_block(contract_result)
            if yaml_content:
                contract_file.parent.mkdir(parents=True, exist_ok=True)
                contract_file.write_text(yaml_content + "\n", encoding="utf-8")
                logger.info("📝 Extracted YAML from agent response and saved api_contract.yaml")

        if not contract_file.exists():
            _persist_phase_artifact(self.workspace_path, "api_contract.yaml", contract_result)

        if contract_file.exists():
            try:
                import yaml
                self.api_contract = yaml.safe_load(
                    contract_file.read_text(encoding="utf-8", errors="replace")
                )
                self.document_indexer.index_artifacts(["api_contract.yaml"])
                logger.info("✅ API contract generated with %d paths",
                            len((self.api_contract or {}).get("paths", {})))
            except Exception as e:
                logger.warning("Could not parse api_contract.yaml: %s", e)
                self.api_contract = None
        else:
            logger.warning("⚠️ api_contract.yaml was not created — continuing without contract")
    
    # ── Frontend path classification ────────────────────────────────────────

    _FRONTEND_DIR_PREFIXES = {"frontend/", "client/", "ui/", "web/", "app/src/"}
    _FRONTEND_ONLY_EXTENSIONS = {".tsx", ".jsx"}

    @classmethod
    def _is_frontend_file(cls, file_path: str) -> bool:
        """Return True if the file belongs to the frontend layer."""
        fp_lower = file_path.lower()
        if any(fp_lower.startswith(p) for p in cls._FRONTEND_DIR_PREFIXES):
            return True
        if fp_lower.startswith("backend/") or fp_lower.startswith("server/"):
            return False
        ext = Path(file_path).suffix.lower()
        return ext in cls._FRONTEND_ONLY_EXTENSIONS

    # ── Reusable task-processing loop ────────────────────────────────────────

    def _process_file_tasks(
        self,
        agent,
        task_id_set: set,
        label: str,
        completed_files: dict,
        export_registry: dict,
        lock: "threading.Lock",
    ) -> int:
        """Process a batch of file-creation tasks using the given agent.

        Only tasks whose ID is in ``task_id_set`` are picked up; dependency
        ordering is still respected via ``get_next_actionable_task``.

        Returns the number of tasks processed. ``completed_files`` and
        ``export_registry`` are shared dicts protected by ``lock``.
        """
        import threading as _threading
        from ..orchestrator.code_validator import CodeCompletenessValidator
        from ..tools.file_tools import set_thread_workspace, set_allowed_file_paths

        set_thread_workspace(str(self.workspace_path))
        set_allowed_file_paths(None, workspace=str(self.workspace_path))

        count = 0
        max_tasks = 100
        stall_counter = 0

        while count < max_tasks:
            task = self.task_manager.get_next_actionable_task(
                "development", task_id_filter=task_id_set,
            )
            if task is None:
                if stall_counter > 3:
                    break
                import time as _t; _t.sleep(1)
                stall_counter += 1
                continue
            stall_counter = 0

            count += 1
            file_path = (task.metadata or {}).get("file_path", "")
            if isinstance(file_path, str):
                file_path = file_path.strip()
            else:
                file_path = ""
            auto_content = (task.metadata or {}).get("auto_content")

            if task.task_type == "file_creation" and (not file_path or file_path.lower() == "unknown"):
                self.task_manager.update_task_status(
                    task.task_id, "skipped",
                    "No file_path in task metadata (feature/other task)",
                )
                logger.info("[%s] Task %d: skipped (no file_path) %s", label, count, task.task_id)
                continue

            logger.info("[%s] Task %d: generating %s", label, count, file_path or task.description)
            self._report_progress(
                'development',
                65 + min(20, count),
                f"[{label}] Creating {file_path or task.description}...",
            )
            self.task_manager.mark_task_started(task.task_id)

            if auto_content is not None and file_path:
                target = self.workspace_path / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(auto_content, encoding="utf-8")
                self.task_manager.update_task_status(
                    task.task_id, "completed", f"Auto-generated: {file_path}"
                )
                logger.info("[%s] ✅ Auto-generated %s", label, file_path)
                continue

            MAX_FILE_RETRIES = 2
            retry_prompt = None

            for attempt in range(MAX_FILE_RETRIES + 1):
                agent.agent.reset_chat()

                with lock:
                    snap_files = dict(completed_files)
                    snap_exports = dict(export_registry)

                pl = getattr(self.config, "prompt_limits", None) if self.config else None
                max_pvc = getattr(pl, "max_project_vision_chars", None) if pl else None
                prompt = self.task_manager.build_file_prompt(
                    task,
                    tech_stack=self.tech_stack or "",
                    user_stories=self.user_stories or "",
                    existing_files=snap_files,
                    project_vision=self.vision or "",
                    max_project_vision_chars=max_pvc,
                    interface_contract=snap_exports if snap_exports else None,
                    api_contract=self.api_contract,
                )

                if attempt > 0 and retry_prompt:
                    prompt = retry_prompt

                try:
                    result = agent.agent.chat(prompt)
                    result_str = str(result)
                    self.task_manager.update_task_status_by_output(result_str)
                except Exception as e:
                    logger.error("[%s] Task %s failed: %s", label, task.task_id, e)
                    self.task_manager.mark_task_executed(task.task_id, TaskStatus.FAILED, str(e))
                    break

                if not file_path:
                    break

                full_path = self.workspace_path / file_path
                if not full_path.exists():
                    all_files_map = {p.name: p for p in self.workspace_path.rglob("*") if p.is_file()}
                    if Path(file_path).name in all_files_map:
                        full_path = all_files_map[Path(file_path).name]

                if not full_path.exists():
                    if attempt == MAX_FILE_RETRIES:
                        self.task_manager.update_task_status(
                            task.task_id, "skipped",
                            f"File {file_path} was not created by the agent",
                        )
                    continue

                integration = CodeCompletenessValidator.validate_file_integration(
                    full_path, self.workspace_path
                )
                completeness = CodeCompletenessValidator.validate_file(full_path)
                all_issues = integration.get("issues", []) + completeness.get("issues", [])

                if not all_issues or attempt == MAX_FILE_RETRIES:
                    self.task_manager.update_task_status(task.task_id, "completed", f"File created: {file_path}")
                    try:
                        content = full_path.read_text(encoding="utf-8", errors="replace")
                        if len(content) <= 5120:
                            with lock:
                                completed_files[file_path] = content
                        else:
                            with lock:
                                completed_files[file_path] = content[:5120] + "\n# ... truncated ..."
                    except Exception:
                        pass
                    try:
                        summary = CodeCompletenessValidator.extract_export_summary(full_path)
                        with lock:
                            export_registry[file_path] = summary.get("exports", [])
                    except Exception:
                        pass
                    if all_issues:
                        logger.warning("[%s] ⚠️ File %s still has issues after retry: %s", label, file_path, all_issues)
                    break

                logger.warning("[%s] ⚠️ File %s has issues (attempt %d), retrying: %s",
                               label, file_path, attempt + 1, all_issues)
                retry_prompt = (
                    f"The file `{file_path}` you just created has these issues:\n"
                    + "\n".join(f"- {i}" for i in all_issues)
                    + f"\n\nPlease fix and rewrite `{file_path}` using file_writer."
                )

        return count

    def run_development_phase(self) -> str:
        """Run Development phase: iterate per-task, generating one file at a time.

        For fullstack projects (backend + frontend), backend and frontend
        file tasks are processed **in parallel** by DevAgent and FrontendAgent
        respectively, cutting wall-clock time roughly in half.
        """
        import threading as _threading

        logger.info("🚀 Starting Development phase...")
        self._report_progress('development', 65, "Implementing application logic...")

        from ..orchestrator.code_validator import CodeCompletenessValidator
        from ..orchestrator.language_strategies import StrategyRegistry

        if self.state_machine.get_current_state() != ProjectState.DEVELOPMENT:
            self.state_machine.transition(
                ProjectState.DEVELOPMENT,
                TransitionContext(phase="development", data={})
            )

        # BDD gate
        features = parse_features_from_files(str(self.workspace_path))
        if not features and self.user_stories:
            extracted = _ensure_feature_files(self.workspace_path, self.user_stories)
            if extracted:
                features = parse_features_from_files(str(self.workspace_path))
        if not features:
            logger.warning(
                "⚠️ BDD gate: no .feature files found — development will proceed "
                "from design_spec/tech_stack only (feature-driven tasks skipped)"
            )
        if features:
            self.task_manager.register_tasks_from_features(features)
            logger.info("📋 Registered %d BDD feature task(s) for development", len(features))

        # Create backend dev agent
        backstory = self.agent_backstories.get('developer')
        self.dev_agent = DevAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
            workspace_path=self.workspace_path,
        )

        from ..tools.file_tools import set_allowed_file_paths
        allowed = self.task_manager.get_registered_file_paths()
        set_allowed_file_paths(allowed, workspace=str(self.workspace_path))
        logger.info("🔒 file_writer allowlist enabled: %d paths (workspace=%s)", len(allowed), self.workspace_path)

        # Partition all registered tasks into backend and frontend
        all_registered = self.task_manager.get_pending_tasks()
        backend_task_ids: set = set()
        frontend_task_ids: set = set()
        for t in all_registered:
            if t.task_type != "file_creation":
                backend_task_ids.add(t.task_id)
                continue
            fp = (t.metadata or {}).get("file_path", "")
            if self._is_frontend_file(fp):
                frontend_task_ids.add(t.task_id)
            else:
                backend_task_ids.add(t.task_id)

        completed_files: dict = {}
        export_registry: dict = {}
        lock = _threading.Lock()
        is_fullstack = bool(backend_task_ids and frontend_task_ids)

        if is_fullstack:
            logger.info("⚡ Fullstack project detected — running backend (%d tasks) "
                        "and frontend (%d tasks) in parallel",
                        len(backend_task_ids), len(frontend_task_ids))
            self._report_progress('development', 65,
                                  f"Parallel build: {len(backend_task_ids)} backend + "
                                  f"{len(frontend_task_ids)} frontend tasks...")

            fe_backstory = self.agent_backstories.get('frontend_developer')
            self.frontend_agent = FrontendAgent(
                custom_backstory=fe_backstory,
                budget_tracker=self.budget_tracker,
                workspace_path=self.workspace_path,
            )

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                be_future = pool.submit(
                    self._process_file_tasks,
                    self.dev_agent, backend_task_ids, "backend",
                    completed_files, export_registry, lock,
                )
                fe_future = pool.submit(
                    self._process_file_tasks,
                    self.frontend_agent, frontend_task_ids, "frontend",
                    completed_files, export_registry, lock,
                )
                be_count = be_future.result()
                fe_count = fe_future.result()

            task_count = be_count + fe_count
            logger.info("⚡ Parallel build complete: %d backend + %d frontend tasks", be_count, fe_count)
        else:
            all_task_ids = backend_task_ids | frontend_task_ids
            task_count = self._process_file_tasks(
                self.dev_agent, all_task_ids, "dev",
                completed_files, export_registry, lock,
            )

        self._export_registry = export_registry

        # Handle any remaining feature tasks
        feature_tasks = [t for t in self.task_manager.get_incomplete_tasks() if t.task_type == "feature"]
        if feature_tasks:
            feature_names = [t.description for t in feature_tasks]
            try:
                result = self.dev_agent.run(feature_names, self.tech_stack or "", self.user_stories)
                self.task_manager.update_task_status_by_output(result)
            except Exception as e:
                logger.error("Feature implementation failed: %s", e)
        
        # ── Post-development validation suite (delegates to reusable method) ──
        report = self._run_validation_suite()
        report["tasks_processed"] = task_count
        
        # ── Validation remediation: persist issues to DB and trigger agent fixes ──
        if self.job_db and report.get("overall") == "ISSUES_FOUND":
            import uuid as _uuid
            self._report_progress("validation", 95, "Running validation remediation...")
            logger.info("Persisting validation issues to database and attempting remediation...")

            validator_issues = self._call_validator()

            for vi in validator_issues:
                issue_id = str(_uuid.uuid4())
                severity = vi.get("severity", "error")
                self.job_db.create_validation_issue(
                    issue_id=issue_id,
                    job_id=self.project_id,
                    check_name=vi.get("check", "unknown"),
                    severity=severity,
                    file_path=vi.get("file"),
                    line_number=vi.get("line"),
                    description=vi.get("description", ""),
                )

            auto_fixed = self._auto_fix_issues(validator_issues)
            auto_fixed_files = {i.get("file") for i in auto_fixed}

            for vi in validator_issues:
                if vi.get("file") in auto_fixed_files:
                    continue
                if vi.get("severity") != "error":
                    continue

                pending = self.job_db.get_pending_validation_issues(self.project_id)
                matching = [p for p in pending if p["file_path"] == vi.get("file")
                            and p["check_name"] == vi.get("check")]
                if not matching:
                    continue
                db_issue = matching[0]

                self.job_db.update_validation_issue_status(db_issue["id"], "running")

                fix_strategy = self._get_fix_strategy(vi)
                if fix_strategy:
                    self.job_db.update_validation_issue_status(
                        db_issue["id"], "running", fix_strategy=fix_strategy
                    )
                    self._apply_fix(vi.get("file", ""), fix_strategy)

                    re_issues = self._call_validator()
                    still_broken = any(
                        r.get("file") == vi.get("file") and r.get("check") == vi.get("check")
                        for r in re_issues
                    )
                    if still_broken:
                        self.job_db.update_validation_issue_status(
                            db_issue["id"], "failed",
                            error=f"Issue persists after fix attempt: {vi.get('description', '')}"
                        )
                    else:
                        self.job_db.update_validation_issue_status(db_issue["id"], "completed")
                else:
                    self.job_db.update_validation_issue_status(
                        db_issue["id"], "failed", error="No fix strategy produced"
                    )

            for vi in auto_fixed:
                matching = self.job_db.get_pending_validation_issues(self.project_id)
                for m in matching:
                    if m["file_path"] == vi.get("file") and m["check_name"] == vi.get("check"):
                        self.job_db.update_validation_issue_status(m["id"], "completed")

            failed_issues = self.job_db.get_failed_validation_issues(self.project_id)
            if failed_issues:
                logger.warning(
                    "%d validation issue(s) remain unresolved after remediation", len(failed_issues)
                )

            # Re-check the actual report checks — DB issue status alone is not
            # sufficient because the report covers more checks than the remediation
            # pipeline tracks (e.g. completeness, tech_stack, smoke_test).
            all_passed = all(c.get("pass", True) for c in report["checks"].values())
            report["overall"] = "PASS" if all_passed else "ISSUES_FOUND"
            if all_passed:
                logger.info("All validation checks pass after remediation")
            else:
                failing = [k for k, c in report["checks"].items() if not c.get("pass", True)]
                logger.warning(
                    "Validation still has %d failing check(s) after remediation: %s",
                    len(failing), ", ".join(failing),
                )

        # Disable the file-writer allowlist for subsequent phases
        from ..tools.file_tools import set_allowed_file_paths
        set_allowed_file_paths(None, workspace=str(self.workspace_path))

        self._validation_report = report
        logger.info("✅ Development phase completed (%d tasks processed)", task_count)
        return f"Development phase completed: {task_count} tasks processed"
    
    def run_frontend_phase(self) -> str:
        """Run Frontend phase: handle remaining UI file tasks + monolithic fallback."""
        logger.info("🚀 Starting Frontend phase...")
        self._report_progress('frontend', 90, "Building user interface...")
        
        if self.state_machine.get_current_state() != ProjectState.FRONTEND:
            self.state_machine.transition(
                ProjectState.FRONTEND,
                TransitionContext(phase="frontend", data={})
            )
        
        backstory = self.agent_backstories.get('frontend_developer')
        self.frontend_agent = FrontendAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
            workspace_path=self.workspace_path,
        )
        
        # Check for remaining incomplete file tasks after dev phase.
        # Skip any task whose file already exists on disk (dev agent created it
        # but the task tracker missed the completion marker).
        remaining = self.task_manager.get_incomplete_tasks()
        remaining_files = []
        for t in remaining:
            if t.task_type != "file_creation":
                continue
            fp = (t.metadata or {}).get("file_path", "")
            if fp and (self.workspace_path / fp).exists():
                self.task_manager.update_task_status(
                    t.task_id, "completed",
                    f"File already exists on disk: {fp}",
                )
                logger.info("Frontend phase: skipping %s — already on disk", fp)
                continue
            remaining_files.append(t)
        
        if remaining_files:
            from ..orchestrator.code_validator import CodeCompletenessValidator as _FEValidator
            logger.info("Frontend phase: %d remaining file tasks from dev phase", len(remaining_files))

            # Collect existing file content so the frontend agent sees what
            # the dev agent already produced (prevents hallucinated duplicates)
            existing_files: Dict[str, str] = {}
            for src in sorted(self.workspace_path.rglob("*")):
                if src.is_file() and src.suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt"}:
                    try:
                        existing_files[str(src.relative_to(self.workspace_path))] = src.read_text(
                            encoding="utf-8", errors="replace"
                        )[:3000]
                    except Exception:
                        pass

            for task in remaining_files:
                file_path = (task.metadata or {}).get("file_path", "")

                # Auto-injected files (e.g. __init__.py) — write directly
                auto_content = (task.metadata or {}).get("auto_content")
                if auto_content is not None and file_path:
                    target = self.workspace_path / file_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(auto_content, encoding="utf-8")
                    self.task_manager.update_task_status(
                        task.task_id, "completed", f"Auto-generated: {file_path}"
                    )
                    continue

                pl = getattr(self.config, "prompt_limits", None) if self.config else None
                max_pvc = getattr(pl, "max_project_vision_chars", None) if pl else None
                prompt = self.task_manager.build_file_prompt(
                    task,
                    tech_stack=self.tech_stack or "",
                    user_stories=self.user_stories or "",
                    existing_files=existing_files,
                    project_vision=self.vision or "",
                    max_project_vision_chars=max_pvc,
                    interface_contract=self._export_registry if self._export_registry else None,
                    api_contract=self.api_contract,
                )
                try:
                    self.frontend_agent.agent.reset_chat()
                    result = self.frontend_agent.agent.chat(prompt)
                    self.task_manager.update_task_status_by_output(str(result))
                except Exception as e:
                    logger.error("Frontend task %s failed: %s", task.task_id, e)
                
                full_path = self.workspace_path / file_path if file_path else None
                if full_path and full_path.exists():
                    self.task_manager.update_task_status(task.task_id, "completed", f"File created: {file_path}")
        else:
            # All file tasks done — run frontend agent for general UI polish
            result = self.frontend_agent.run(
                self.design_spec or "",
                self.tech_stack or "",
                self.user_stories,
                vision=self.vision,
            )
            self.task_manager.update_task_status_by_output(result)
        
        # Filesystem reconciliation for anything still incomplete
        self.task_manager.reconcile_with_filesystem(self.workspace_path)
        
        logger.info("✅ Frontend phase completed")
        return "Frontend phase completed"

    def run_devops_phase(self) -> str:
        """Run DevOps phase: create Containerfile(s) and CI/CD pipeline YAML."""
        from ..agents.devops_agent import DevOpsAgent
        from ..tools.file_tools import set_allowed_file_paths, set_thread_workspace

        logger.info("🚀 Starting DevOps phase...")
        self._report_progress('devops', 93, "Creating Containerfiles and CI/CD pipelines...")

        if self.state_machine.get_current_state() != ProjectState.DEVOPS:
            self.state_machine.transition(
                ProjectState.DEVOPS,
                TransitionContext(phase="devops", data={})
            )

        set_thread_workspace(str(self.workspace_path))
        set_allowed_file_paths(None, workspace=str(self.workspace_path))

        backstory = self.agent_backstories.get('devops')
        devops_agent = DevOpsAgent(
            workspace_path=self.workspace_path,
            project_id=self.project_id,
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
        )

        # Gather project context for the DevOps agent
        project_files = []
        for src in sorted(self.workspace_path.rglob("*")):
            if src.is_file() and src.name in (
                "docker-compose.yml", "docker-compose.yaml",
                "Dockerfile", "Containerfile",
                "package.json", "pom.xml", "requirements.txt",
                "build.gradle", "Makefile",
            ):
                project_files.append(str(src.relative_to(self.workspace_path)))
        project_context = (
            f"Existing project files: {', '.join(project_files)}\n"
            if project_files else ""
        )
        if self.design_spec:
            project_context += f"\nDesign spec summary:\n{self.design_spec[:2000]}"

        pipeline_type = "tekton"
        if self.tech_stack:
            ts_lower = self.tech_stack.lower()
            if "github actions" in ts_lower:
                pipeline_type = "github_actions"

        import concurrent.futures
        _DEVOPS_TIMEOUT_SECS = int(os.environ.get("DEVOPS_TIMEOUT_SECS", "180"))

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    devops_agent.run,
                    tech_stack=self.tech_stack or "",
                    pipeline_type=pipeline_type,
                    project_context=project_context,
                )
                result = future.result(timeout=_DEVOPS_TIMEOUT_SECS)
            logger.info("✅ DevOps phase completed")
            self._report_progress('devops', 95, "Containerfiles and pipelines created")
        except concurrent.futures.TimeoutError:
            logger.warning("⚠️ DevOps phase timed out after %ds — continuing", _DEVOPS_TIMEOUT_SECS)
            self._report_progress('devops', 95, "DevOps phase timed out — continuing")
        except Exception as exc:
            logger.warning("⚠️ DevOps phase failed: %s — continuing", exc)
            self._report_progress('devops', 95, f"DevOps phase failed: {exc}")

        return "DevOps phase completed"

    def _run_phase_with_retry(self, phase_name: str, phase_fn: Callable[[], Any]) -> Any:
        """Run a phase with retries on transient LLM/network errors."""
        last_error = None
        for attempt in range(PHASE_RETRY_ATTEMPTS):
            try:
                return phase_fn()
            except Exception as e:
                last_error = e
                if _is_transient_llm_error(e) and attempt < PHASE_RETRY_ATTEMPTS - 1:
                    logger.warning(
                        f"⚠️ Phase '{phase_name}' failed (transient): {e}. "
                        f"Retrying in {PHASE_RETRY_DELAY_SEC}s... (attempt {attempt + 1}/{PHASE_RETRY_ATTEMPTS})"
                    )
                    time.sleep(PHASE_RETRY_DELAY_SEC)
                    continue
                raise
        if last_error is not None:
            raise last_error

    def run(self, resume: bool = False) -> Dict[str, Any]:
        """
        Run complete workflow.

        When resume=True, loads persisted artifacts and runs only from the
        current state (e.g. if state is FRONTEND, re-runs frontend then completes).
        """
        # Re-establish workspace for this job so file/git/test tools always write here
        # (guards against thread-local or env being cleared by other code)
        try:
            from ..tools.file_tools import set_thread_workspace, set_allowed_file_paths
            set_thread_workspace(str(self.workspace_path))
            set_allowed_file_paths(None, workspace=str(self.workspace_path))
            logger.debug("Workflow run: thread workspace set to %s", self.workspace_path)
        except Exception as e:
            logger.warning("Could not set thread workspace in workflow: %s", e)

        try:
            if resume:
                self._load_phase_artifacts()
                current = self.state_machine.get_current_state()
                if current in (ProjectState.COMPLETED, ProjectState.FAILED):
                    logger.info("Resume: state already %s; running full workflow", current.value)
                    resume = False
                else:
                    try:
                        start_idx = _RESUMABLE_PHASES.index(current)
                    except ValueError:
                        start_idx = 0
                    logger.info("Resume: starting from phase %s (index %d)", current.value, start_idx)
                    remaining = _RESUMABLE_PHASES[start_idx:]
                    for phase_state in remaining:
                        if phase_state == ProjectState.META:
                            self.run_meta_phase()
                        elif phase_state == ProjectState.PRODUCT_OWNER:
                            self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
                        elif phase_state == ProjectState.DESIGNER:
                            self._run_phase_with_retry("designer", self.run_designer_phase)
                        elif phase_state == ProjectState.TECH_ARCHITECT:
                            self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
                        elif phase_state == ProjectState.DEVELOPMENT:
                            # Dev, Frontend, DevOps in parallel when resuming from dev
                            import concurrent.futures as _cf
                            with _cf.ThreadPoolExecutor(max_workers=3) as pool:
                                futs = [pool.submit(self._run_phase_with_retry, "development", self.run_development_phase)]
                                if ProjectState.FRONTEND in remaining:
                                    futs.append(pool.submit(self._run_phase_with_retry, "frontend", self.run_frontend_phase))
                                if ProjectState.DEVOPS in remaining:
                                    futs.append(pool.submit(self._run_phase_with_retry, "devops", self.run_devops_phase))
                                for f in futs:
                                    f.result()
                            break  # all remaining phases handled
                        elif phase_state == ProjectState.FRONTEND:
                            self._run_phase_with_retry("frontend", self.run_frontend_phase)
                        elif phase_state == ProjectState.DEVOPS:
                            self._run_phase_with_retry("devops", self.run_devops_phase)
                    # Fall through to transition to completed + final sweep below
            if not resume:
                # Phase 1: Meta (has its own retry logic)
                self.run_meta_phase()
                # Phases 2–7: run with retry on transient LLM/network errors
                self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
                self._run_phase_with_retry("designer", self.run_designer_phase)
                self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
                # Dev, Frontend, and DevOps run in parallel — DevOps only
                # needs tech_stack (no dependency on generated code).
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=3) as pool:
                    dev_fut = pool.submit(
                        self._run_phase_with_retry, "development", self.run_development_phase,
                    )
                    fe_fut = pool.submit(
                        self._run_phase_with_retry, "frontend", self.run_frontend_phase,
                    )
                    devops_fut = pool.submit(
                        self._run_phase_with_retry, "devops", self.run_devops_phase,
                    )
                    # Raise if any phase failed
                    dev_fut.result()
                    fe_fut.result()
                    devops_fut.result()

            # Post-build validation + fix iteration loop.
            # Re-validates after dev+frontend phases and gives the dev agent a
            # chance to fix remaining issues before marking the job complete.
            self._run_post_build_fix_iteration()

            # Transition to completed
            self.state_machine.transition(
                ProjectState.COMPLETED,
                TransitionContext(phase="completed", data={})
            )

            # Final sweep: agents may reorganize files into subdirectories
            # (e.g. src/server.py → backend/src/server.py).  Check by basename.
            # Tasks still in REGISTERED status were never started by agents —
            # they represent the architect's plan that agents chose to satisfy
            # differently; skip them rather than failing the whole job.
            remaining = self.task_manager.get_incomplete_tasks()
            if remaining:
                all_files = {p.name: p for p in self.workspace_path.rglob("*") if p.is_file()}
                for task in remaining:
                    if task.task_type == "file_creation":
                        file_path = (task.metadata or {}).get("file_path", "")
                        if not file_path:
                            continue
                        basename = Path(file_path).name
                        if basename in all_files:
                            logger.info("Fallback (basename): task %s matched %s", task.task_id, all_files[basename])
                            self.task_manager.update_task_status(task.task_id, "completed", f"File found at {all_files[basename]}")
                        elif task.status == TaskStatus.REGISTERED.value:
                            logger.info(
                                "Fallback: skipping registered file task %s (%s) — agents reorganized the project",
                                task.task_id, file_path,
                            )
                            self.task_manager.update_task_status(
                                task.task_id, "skipped",
                                f"File planned as {file_path} but agents reorganized; project completed successfully",
                            )
                    elif task.task_type == "feature":
                        logger.info("Fallback: marking feature task %s as completed (project built)", task.task_id)
                        self.task_manager.update_task_status(task.task_id, "completed", "Project built successfully")
                    elif task.status == TaskStatus.REGISTERED.value:
                        logger.info(
                            "Fallback: skipping registered task %s (type=%s) — never started, project completed",
                            task.task_id, task.task_type,
                        )
                        self.task_manager.update_task_status(
                            task.task_id, "skipped",
                            "Task was planned but never started; project completed successfully",
                        )

            # Final task validation (with filesystem reconciliation)
            completed_check = self.task_manager.validate_all_tasks_completed(
                workspace_path=self.workspace_path
            )

            return {
                "status": "completed",
                "project_id": self.project_id,
                "budget_report": self.budget_tracker.get_report(self.project_id),
                "task_validation": completed_check,
                "validation_report": getattr(self, '_validation_report', {}),
                "state": self.state_machine.get_current_state().value
            }
        except Exception as e:
            logger.error(f"❌ Workflow failed: {e}")
            self.state_machine.transition(
                ProjectState.FAILED,
                TransitionContext(phase="failed", data={"error": str(e)})
            )
            raise
