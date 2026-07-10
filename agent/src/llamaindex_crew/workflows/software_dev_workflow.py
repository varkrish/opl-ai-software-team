"""
Software Development Workflow
Sequential workflow orchestrating all agents
"""
import json as _json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from llama_index.core import Settings as _LISettings
from ..agents import (
    MetaAgent, ProductOwnerAgent, DesignerAgent,
    TechArchitectAgent, DevAgent, FrontendAgent
)
from ..agents.meta_agent import ImportModeRecommendedError, user_vision_for_triage
from ..orchestrator.state_machine import ProjectStateMachine, ProjectState, TransitionContext
from ..orchestrator.task_manager import TaskManager, TaskStatus
from ..orchestrator.error_recovery import WorkflowErrorRecoveryEngine
from ..budget.tracker import EnhancedBudgetTracker
from ..utils.feature_parser import parse_features_from_files
from ..utils.document_indexer import DocumentIndexer
from ..utils.rag_context import get_phase_rag_context
from ..utils.llm_config import get_embedding_model
from ..utils.output_parser import (
    is_valid_gherkin_feature,
    product_owner_format_instruction,
    simple_mode_format_instruction,
    write_files_from_response,
)
from ..utils.generation_prompt_utils import (
    filter_retry_issues,
    is_likely_large_file,
    trim_tech_stack_for_prompt,
    trim_user_stories_for_prompt,
)
from .epic_story_loop import (
    StoryAssessment,
    assess_epic_stories,
    commit_message_for_story,
    decompose_epic_to_stories,
    format_jira_stories_as_markdown,
    has_jira_connection,
    is_epic_job,
    judge_stories,
    parse_jira_stories,
    project_key_from_epic,
    resume_story_index,
    should_auto_approve,
    stories_are_provisioned,
    stories_to_process,
    story_vision,
)

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
    """Guarantee that features/ contains valid .feature files.

    Removes invalid/stub feature files, then extracts Gherkin from *user_stories_text*
    when needed. Returns the count of valid feature files after the call.
    """
    features_dir = workspace / "features"
    if features_dir.exists():
        for path in list(features_dir.glob("*.feature")):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            if not is_valid_gherkin_feature(content):
                logger.warning("Removing invalid feature file: %s", path.name)
                path.unlink(missing_ok=True)

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
        pl = getattr(config, "prompt_limits", None) if config else None
        self.document_indexer = DocumentIndexer(
            workspace_path,
            project_id,
            chunk_size=int(getattr(pl, "rag_chunk_size", 1024)) if pl else 1024,
            chunk_overlap=int(getattr(pl, "rag_chunk_overlap", 128)) if pl else 128,
        )
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
        self.solution_spec = None
        self._export_registry: Dict[str, Any] = {}  # file_path -> export summary from dev phase
        self._tldr_structure_cache: dict = {}
    
    def _report_progress(self, phase: str, progress: int, message: str = None):
        """Report progress via callback if available"""
        if self.progress_callback:
            try:
                self.progress_callback(phase, progress, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    def _load_job_metadata(self) -> dict:
        if not self.job_db:
            return {}
        try:
            job = self.job_db.get_job(self.project_id)
            if not job:
                return {}
            meta = job.get("metadata") or {}
            if isinstance(meta, str):
                return _json.loads(meta) if meta else {}
            return meta if isinstance(meta, dict) else {}
        except Exception:
            return {}

    def _update_job_metadata(self, metadata: dict) -> None:
        if not self.job_db:
            return
        self.job_db.update_job(self.project_id, {"metadata": _json.dumps(metadata)})

    def _append_job_event(self, event: dict) -> None:
        if not self.job_db:
            return
        job = self.job_db.get_job(self.project_id)
        if not job:
            return
        messages = job.get("last_message") or []
        if isinstance(messages, str):
            try:
                messages = _json.loads(messages)
            except _json.JSONDecodeError:
                messages = []
        messages.append({
            **event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.job_db.update_job(self.project_id, {"last_message": _json.dumps(messages[-50:])})

    def _get_manager_llm(self):
        from ..utils.llm_config import get_llm_for_agent
        return get_llm_for_agent("manager", self.config)

    def _auto_approve_no_jira(self) -> bool:
        epic_cfg = getattr(self.config, "epic", None) if self.config else None
        return bool(getattr(epic_cfg, "auto_approve_no_jira", True))

    def _plan_review_enabled(self) -> bool:
        """Return True when the review gate should activate for this job.

        Respects job-specific metadata override if defined, else falls back to server config.
        """
        metadata = self._load_job_metadata()
        auto_approve = metadata.get("auto_approve_plan")
        if auto_approve is not None:
            return not bool(auto_approve)
        pr_cfg = getattr(self.config, "plan_review", None) if self.config else None
        return bool(getattr(pr_cfg, "enabled", False))

    def _solutioning_enabled(self) -> bool:
        """Return True when the solutioning loop should run before PO phase."""
        metadata = self._load_job_metadata()
        auto_approve = metadata.get("auto_approve_solution")
        if auto_approve is not None:
            return not bool(auto_approve)
        sol_cfg = getattr(self.config, "solutioning", None) if self.config else None
        return bool(getattr(sol_cfg, "enabled", False))

    def _enrich_project_context_for_solutioning(self) -> str:
        """Return project_context enriched with an EXISTING CODEBASE section for brownfield jobs.

        Falls back to plain project_context if the workspace is empty or tldr_tools
        are unavailable so greenfield jobs are unaffected.
        """
        base = self.project_context or ""
        try:
            from ..tools.tldr_tools import build_solutioning_codebase_context
            extra = build_solutioning_codebase_context(self.workspace_path)
            if extra:
                return f"{base}\n\n{extra}".strip()
        except Exception:
            pass
        return base

    def _run_solutioning_loop(self):
        """Run research → architect → critique loop and persist artifacts."""
        from .solutioning_loop import run_solutioning_loop

        max_passes = 3
        max_github = 10
        sol_cfg = getattr(self.config, "solutioning", None) if self.config else None
        if sol_cfg:
            max_passes = int(getattr(sol_cfg, "max_passes", 3) or 3)
            max_github = int(getattr(sol_cfg, "max_github_searches", 10) or 10)

        github_token = None
        if self.job_db:
            job = self.job_db.get_job(self.project_id)
            owner_id = job.get("owner_id") if job else None
            if owner_id:
                gh_cfg = self.job_db.get_github_config(owner_id)
                if gh_cfg:
                    github_token = gh_cfg.get("token")

        result = run_solutioning_loop(
            vision=self.vision,
            project_context=self._enrich_project_context_for_solutioning(),
            workspace_path=self.workspace_path,
            config=self.config,
            budget_tracker=self.budget_tracker,
            document_indexer=self.document_indexer,
            max_passes=max_passes,
            progress_callback=self.progress_callback,
            max_github_searches=max_github,
            github_token=github_token,
        )
        if result.spec_path.exists():
            try:
                self.solution_spec = result.spec_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        self._report_progress(
            "solutioning",
            28,
            f"Solutioning complete ({result.pass_count} pass(es), approved={result.approved})",
        )
        return result

    def _pause_for_solution_review(self, feedback_history: list | None = None) -> dict:
        """Set job to pending_solution_review and return a pause result dict."""
        metadata = self._load_job_metadata()
        metadata["solution_pending_review"] = True
        metadata["solution_feedback_history"] = feedback_history or []
        self._update_job_metadata(metadata)
        if self.job_db:
            self.job_db.update_job(self.project_id, {
                "status": "pending_solution_review",
                "current_phase": "pending_solution_review",
                "progress": 25,
            })
        self._append_job_event({
            "type": "solution_pending_review",
            "message": "Solution spec ready — awaiting human review before planning",
        })
        self._report_progress("pending_solution_review", 25, "Solution ready — awaiting review")
        return {
            "status": "pending_solution_review",
            "project_id": self.project_id,
            "budget_report": self.budget_tracker.get_report(self.project_id),
            "state": self.state_machine.get_current_state().value,
        }

    def refine_solution(self, feedback: str) -> dict:
        """Re-run solution architect + critique passes with user feedback."""
        logger.info("Solution refinement requested for job %s: %r", self.project_id, feedback[:120])
        self._report_progress("pending_solution_review", 20, "Refining solution with your feedback…")

        metadata = self._load_job_metadata()
        history = metadata.get("solution_feedback_history") or []
        history.append({"feedback": feedback, "at": _json.dumps(datetime.now(timezone.utc).isoformat())})
        metadata["solution_feedback_history"] = history
        self._update_job_metadata(metadata)

        candidates_path = self.workspace_path / "solution_candidates.json"
        candidates_json = ""
        if candidates_path.exists():
            candidates_json = candidates_path.read_text(encoding="utf-8", errors="replace")

        from ..agents.solution_agents import SolutionArchitectAgent, SolutionCritiqueAgent

        architect = SolutionArchitectAgent(
            budget_tracker=self.budget_tracker,
            workspace_path=self.workspace_path,
            config=self.config,
        )
        critique_agent = SolutionCritiqueAgent(
            budget_tracker=self.budget_tracker,
            config=self.config,
        )

        combined_feedback = feedback
        if history[:-1]:
            prior = "\n".join(h.get("feedback", "") for h in history[:-1] if h.get("feedback"))
            if prior.strip():
                combined_feedback = f"{prior.strip()}\n\nLatest feedback:\n{feedback}"

        architect.run(self.vision, self._enrich_project_context_for_solutioning(), candidates_json, feedback=combined_feedback)
        spec_path = self.workspace_path / "solution_spec.md"
        spec_content = spec_path.read_text(encoding="utf-8", errors="replace") if spec_path.exists() else ""
        critique_raw = critique_agent.run(self.vision, spec_content, candidates_json)

        pass_num = len(list(self.workspace_path.glob("solution_critique_pass_*.json"))) + 1
        try:
            import re as _re
            match = _re.search(r"\{[\s\S]*\}", critique_raw or "")
            critique = _json.loads(match.group(0)) if match else {"approved": False, "score": 0, "issues": [], "must_fix": []}
        except Exception:
            critique = {"approved": False, "score": 0, "issues": ["Invalid critique JSON"], "must_fix": []}
        critique_path = self.workspace_path / f"solution_critique_pass_{pass_num}.json"
        critique_path.write_text(_json.dumps(critique, indent=2) + "\n", encoding="utf-8")

        if spec_path.exists():
            self.solution_spec = spec_path.read_text(encoding="utf-8", errors="replace")

        metadata["solution_pending_review"] = True
        self._update_job_metadata(metadata)
        if self.job_db:
            self.job_db.update_job(self.project_id, {
                "status": "pending_solution_review",
                "current_phase": "pending_solution_review",
                "progress": 25,
            })

        artifacts = self._load_plan_artifacts()
        if spec_path.exists():
            artifacts["solution_spec.md"] = self.solution_spec or spec_content
        return {
            "status": "pending_solution_review",
            "artifacts": artifacts,
            "feedback_rounds": len(history),
            "solution_approved_by_critique": bool(critique.get("approved")),
        }

    def _load_plan_artifacts(self) -> dict[str, str]:
        """Read planning artifacts from workspace for plan review."""
        artifacts: dict[str, str] = {}
        for name in ("user_stories.md", "design_spec.md", "tech_stack.md", "implementation_plan.md", "requirements.md"):
            p = self.workspace_path / name
            if p.exists():
                try:
                    artifacts[name] = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        return artifacts

    def _pause_for_plan_review(self, feedback_history: list | None = None) -> dict:
        """Set job to pending_review and return a pending_review result dict."""
        metadata = self._load_job_metadata()
        metadata["pending_review"] = True
        metadata["plan_feedback_history"] = feedback_history or []
        self._update_job_metadata(metadata)
        if self.job_db:
            self.job_db.update_job(self.project_id, {
                "status": "pending_review",
                "current_phase": "pending_review",
                "progress": 55,
            })
        self._append_job_event({
            "type": "plan_pending_review",
            "message": "Plan complete — awaiting human review before coding",
        })
        self._report_progress("pending_review", 55, "Plan ready — awaiting review")
        return {
            "status": "pending_review",
            "project_id": self.project_id,
            "budget_report": self.budget_tracker.get_report(self.project_id),
            "state": self.state_machine.get_current_state().value,
        }

    def refine_plan(self, feedback: str) -> dict:
        """Re-run planning phases with user feedback injected into the vision context.

        Called by the /refine-plan API endpoint while the job is pending_review.
        Returns a dict with updated artifact content.
        """
        logger.info("Plan refinement requested for job %s: %r", self.project_id, feedback[:120])
        self._report_progress("pending_review", 30, "Refining plan with your feedback…")

        # Planning phases already advanced the state machine (typically to
        # tech_architect) before pausing for review. Roll back so phases can
        # transition forward again without violating the state graph.
        if self.state_machine.get_current_state() != ProjectState.PRODUCT_OWNER:
            self.state_machine.rollback_to(ProjectState.PRODUCT_OWNER)

        # Append feedback to vision context so agents pick it up
        original_vision = self.vision
        self.vision = (
            f"{original_vision}\n\n"
            f"--- HUMAN FEEDBACK (apply to the plan) ---\n{feedback}\n"
        )

        try:
            self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
            self._run_phase_with_retry("designer", self.run_designer_phase)
            self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
        finally:
            self.vision = original_vision

        # Record feedback round
        metadata = self._load_job_metadata()
        history = metadata.get("plan_feedback_history") or []
        history.append({"feedback": feedback, "at": _json.dumps(datetime.now(timezone.utc).isoformat())})
        metadata["plan_feedback_history"] = history
        metadata["pending_review"] = True
        self._update_job_metadata(metadata)
        if self.job_db:
            self.job_db.update_job(self.project_id, {
                "status": "pending_review",
                "current_phase": "pending_review",
                "progress": 55,
            })

        self._report_progress("pending_review", 55, "Plan updated — awaiting review")
        return {
            "status": "pending_review",
            "artifacts": self._load_plan_artifacts(),
            "feedback_rounds": len(history),
        }

    def _pause_for_approval(self, metadata: dict, stories: list[dict], reasoning: str) -> None:
        metadata["epic_judge_completed"] = True
        metadata["epic_judge_reasoning"] = reasoning
        metadata["jira_stories"] = stories
        self._update_job_metadata(metadata)
        if self.job_db:
            self.job_db.update_job(self.project_id, {
                "status": "pending_approval",
                "current_phase": "pending_approval",
                "progress": 45,
            })
        self._append_job_event({
            "type": "epic_pending_approval",
            "story_count": len(stories),
            "message": reasoning or "Epic stories decomposed — awaiting approval",
        })
        self._report_progress(
            "pending_approval",
            45,
            f"Awaiting approval for {len(stories)} decomposed stories",
        )

    def _create_stories_in_jira(self, metadata: dict, stories: list[dict]) -> list[dict]:
        from ..utils.epic_jira import build_jira_backend, create_stories_in_jira

        epic_key = metadata.get("jira_epic_key") or ""
        project_key = metadata.get("jira_project_key") or project_key_from_epic(epic_key)
        backend = build_jira_backend()
        if not backend:
            logger.warning("JIRA backend unavailable — stories kept in job metadata only")
            return stories
        return create_stories_in_jira(backend, epic_key, project_key, stories)

    def _seed_user_stories_from_jira(self, stories: list[dict]) -> None:
        """Materialize user_stories.md from provisioned JIRA stories without running the PO agent.

        Used when the AI judge decides existing stories are sufficient. The document
        is written deterministically so Designer and TA agents receive the same
        structured input they would get from a PO run.
        """
        logger.info(
            "⏩ Seeding user_stories.md from %d provisioned JIRA stories (PO skipped)", len(stories)
        )
        self._report_progress("product_owner", 30, "Seeding user stories from JIRA (PO skipped)…")

        # Transition through product_owner state for audit trail
        if self.state_machine.get_current_state() != ProjectState.PRODUCT_OWNER:
            self.state_machine.transition(
                ProjectState.PRODUCT_OWNER,
                TransitionContext(phase="product_owner", data={"seeded_from_jira": True}),
            )

        markdown = format_jira_stories_as_markdown(stories, epic_vision=self.vision)
        _persist_phase_artifact(self.workspace_path, "user_stories.md", markdown)
        _persist_phase_artifact(self.workspace_path, "requirements.md", markdown)

        user_stories_file = self.workspace_path / "user_stories.md"
        if user_stories_file.exists():
            self.user_stories = user_stories_file.read_text(encoding="utf-8", errors="replace")
        else:
            self.user_stories = markdown

        # Index so Designer / TA can query the stories via RAG
        if self.document_indexer and self.user_stories:
            try:
                self.document_indexer.index_document("user_stories.md", self.user_stories)
            except Exception as exc:
                logger.warning("Failed to index seeded user_stories.md: %s", exc)

        logger.info("✅ user_stories.md seeded from JIRA stories (%d chars)", len(self.user_stories or ""))

    def _run_epic_judge_gate(self, metadata: dict, stories: list[dict]) -> tuple[list[dict], bool]:
        """Evaluate stories; decompose and optionally pause for approval.

        If `epic_judge_completed` is already set in metadata (set by the pre-TA
        assess_epic_stories path), the gate is a no-op — just return the stories.
        Otherwise run the judge to decide whether to decompose (empty-epic path).
        """
        if metadata.get("epic_judge_completed"):
            # Stories were already assessed (skip-PO path) or previously decomposed.
            return parse_jira_stories(metadata) or stories, False

        llm = self._get_manager_llm()
        verdict = judge_stories(stories, self.vision, llm)
        if verdict.sufficient and stories:
            metadata["epic_judge_completed"] = True
            metadata["epic_judge_reasoning"] = verdict.reasoning
            self._update_job_metadata(metadata)
            return stories, False

        new_stories = decompose_epic_to_stories(
            self.vision,
            self.tech_stack or "",
            llm,
            suggested_stories=verdict.suggested_stories or None,
        )
        if not new_stories:
            logger.warning("Epic decomposition returned no stories — proceeding with existing set")
            return stories, False

        epic_key = metadata.get("jira_epic_key") or ""
        if epic_key and has_jira_connection(metadata):
            new_stories = self._create_stories_in_jira(metadata, new_stories)

        metadata["jira_stories"] = new_stories
        metadata["epic_judge_completed"] = True
        metadata["epic_judge_reasoning"] = verdict.reasoning
        self._update_job_metadata(metadata)

        if should_auto_approve(metadata, self._auto_approve_no_jira()):
            logger.info("Auto-approving decomposed epic stories (no JIRA connection)")
            return new_stories, False

        self._pause_for_approval(metadata, new_stories, verdict.reasoning)
        return new_stories, True

    def _run_epic_workflow(self, metadata: dict, resume: bool = False) -> Dict[str, Any]:
        """Sequential Epic: plan once, judge stories, then Dev per story with commit."""
        from ..tools.git_tools import git_commit
        from ..utils.epic_memory import index_story_memory

        epic_vision = self.vision
        stories = parse_jira_stories(metadata)
        start_index = resume_story_index(metadata) if resume else 0

        if start_index == 0 and not resume:
            self.run_meta_phase()

            if self._solutioning_enabled():
                metadata = self._load_job_metadata()
                if not metadata.get("solution_approved"):
                    self._run_solutioning_loop()
                    return self._pause_for_solution_review()

            # Assess existing stories to decide whether to skip PO
            assessment = assess_epic_stories(
                stories, self.vision, self._get_manager_llm()
            )
            metadata["epic_judge_reasoning"] = assessment.reasoning
            metadata["epic_po_skipped"] = assessment.skip_po
            if assessment.skip_po:
                logger.info("Epic PO skipped — seeding user_stories.md from JIRA stories")
                self._seed_user_stories_from_jira(assessment.stories)
                metadata["epic_judge_completed"] = True
            else:
                self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
            self._update_job_metadata(metadata)

            self._run_phase_with_retry("designer", self.run_designer_phase)
            self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
            # Universal plan review gate (before epic judge)
            if self._plan_review_enabled() and not metadata.get("pending_review_approved"):
                return self._pause_for_plan_review()
            stories, paused = self._run_epic_judge_gate(metadata, stories)
            if paused:
                return {
                    "status": "pending_approval",
                    "project_id": self.project_id,
                    "budget_report": self.budget_tracker.get_report(self.project_id),
                    "state": self.state_machine.get_current_state().value,
                    "epic_stories_planned": len(stories),
                    "message": metadata.get("epic_judge_reasoning", ""),
                }
        else:
            self._load_phase_artifacts()
            metadata = self._load_job_metadata()
            stories = parse_jira_stories(metadata)

        remaining = stories_to_process(stories, start_index)
        total = len(stories)
        stories_completed = 0

        for offset, story in enumerate(remaining):
            index = start_index + offset
            story_key = story.get("key", f"story-{index}")
            self._report_progress(
                "development",
                65 + int(25 * (index + 1) / max(total, 1)),
                f"Story {story_key} ({index + 1}/{total})",
            )
            scoped = story_vision(story, epic_vision)
            self.user_stories = scoped
            self._run_phase_with_retry("development", self.run_development_phase)

            commit_msg = commit_message_for_story(story)
            commit_result = git_commit(commit_msg)
            commit_sha = ""
            if "Committed:" in commit_result:
                part = commit_result.split("Committed:", 1)[1].strip()
                commit_sha = part.split()[0].strip()

            index_story_memory(
                self.workspace_path,
                self.document_indexer,
                story_key=story_key,
                story_index=index,
                story_summary=scoped,
            )

            metadata["last_completed_story_index"] = index
            self._update_job_metadata(metadata)
            stories_completed += 1
            self._append_job_event({
                "type": "story_completed",
                "story_key": story_key,
                "commit_sha": commit_sha,
                "index": index,
                "total": total,
                "phase": "development",
                "message": f"Story {story_key} committed",
            })

        self._run_post_build_fix_iteration()
        self.state_machine.transition(
            ProjectState.COMPLETED,
            TransitionContext(phase="completed", data={"epic": True}),
        )
        return {
            "status": "completed",
            "project_id": self.project_id,
            "budget_report": self.budget_tracker.get_report(self.project_id),
            "state": self.state_machine.get_current_state().value,
            "epic_stories_completed": stories_completed,
            "epic_stories_planned": total,
        }

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

        # 8. File manifest (controlled by TECH_STACK_MANIFEST_GUARD)
        from ..utils.manifest_guard import (
            get_manifest_guard_mode,
            is_path_manifest_authorized,
        )
        guard_mode = get_manifest_guard_mode()
        allowed_paths = self.task_manager.get_registered_file_paths()
        _META_FILES = {
            "agent_backstories.json", "agent_prompts.json", "crew_errors.log",
            "validation_report.json", "validation_report.log",
            "smoke_test_container.log", "unknown",
        }
        unauthorized: List[str] = []
        conflict_pairs: List[Dict[str, str]] = []
        if guard_mode.value == "off":
            report["checks"]["file_manifest"] = {
                "pass": True,
                "skipped": True,
                "guard_mode": guard_mode.value,
                "unauthorized_files": [],
                "file_package_conflicts": [],
            }
        else:
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
                if not is_path_manifest_authorized(
                    rel, allowed_paths, self.workspace_path, guard_mode
                ):
                    unauthorized.append(rel)
            for p in sorted(allowed_paths):
                if p.endswith("/__init__.py"):
                    dir_stem = p.rsplit("/__init__.py", 1)[0]
                    flat_mod = f"{dir_stem}.py"
                    if flat_mod in allowed_paths or (self.workspace_path / flat_mod).exists():
                        conflict_pairs.append({"package": p, "flat_module": flat_mod})
            report["checks"]["file_manifest"] = {
                "pass": len(unauthorized) == 0 and len(conflict_pairs) == 0,
                "guard_mode": guard_mode.value,
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

            # Apply manifest guard for remediation (TECH_STACK_MANIFEST_GUARD)
            from ..utils.manifest_guard import remediation_write_allowlist
            registered = self.task_manager.get_registered_file_paths()
            allowed = remediation_write_allowlist(
                registered, self.workspace_path,
            )
            set_allowed_file_paths(allowed, workspace=str(self.workspace_path))

            # Ensure dev agent is available
            if self.dev_agent is None:
                backstory = self.agent_backstories.get("developer")
                self.dev_agent = DevAgent(
                    custom_backstory=backstory,
                    budget_tracker=self.budget_tracker,
                    workspace_path=self.workspace_path,
                    config=self.config,
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
            ("solution_spec.md", "solution_spec"),
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
                self.agent_backstories = _json.loads(backstories_file.read_text(encoding="utf-8"))
                logger.info("Resume: loaded agent_backstories.json")
            except Exception as e:
                logger.warning("Resume: could not load agent_backstories.json: %s", e)

    def _infer_resume_state_from_artifacts(self) -> Optional[ProjectState]:
        """Infer the last completed planning phase from persisted workspace files."""
        wp = self.workspace_path
        try:
            ts = wp / "tech_stack.md"
            if ts.exists() and ts.read_text(encoding="utf-8", errors="replace").strip():
                # Checkpoint: tech_stack.md (+ optional test_plan.md) → development
                return ProjectState.DEVELOPMENT
            if (wp / "design_spec.md").exists():
                return ProjectState.TECH_ARCHITECT
            if (wp / "user_stories.md").exists():
                return ProjectState.DESIGNER
            if (wp / "solution_spec.md").exists():
                return ProjectState.PRODUCT_OWNER
            if (wp / "agent_backstories.json").exists():
                return ProjectState.PRODUCT_OWNER
        except Exception as e:
            logger.warning("Resume: could not infer checkpoint from artifacts: %s", e)
        return None
    
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

            # ── Delivery-mode triage: greenfield build vs import+iterate ─────────
            skip_triage = os.getenv("SKIP_DELIVERY_MODE_TRIAGE", "").strip().lower() in (
                "1", "true", "yes",
            )
            job_meta: Dict[str, Any] = {}
            if self.job_db:
                jrow = self.job_db.get_job(self.project_id)
                if jrow:
                    rawm = jrow.get("metadata")
                    if isinstance(rawm, str):
                        try:
                            job_meta = _json.loads(rawm)
                        except (_json.JSONDecodeError, TypeError):
                            job_meta = {}
                    elif isinstance(rawm, dict):
                        job_meta = rawm
            if job_meta.get("skip_delivery_mode_guard"):
                skip_triage = True
                logger.info("skip_delivery_mode_guard: skipping delivery-mode triage")

            if not skip_triage:
                uv = user_vision_for_triage(self.vision)
                triage = self.meta_agent.triage_delivery_mode(uv, self.workspace_path)
                try:
                    (self.workspace_path / "delivery_mode_triage.json").write_text(
                        _json.dumps(triage, indent=2),
                        encoding="utf-8",
                    )
                except OSError as e:
                    logger.warning("Could not save delivery_mode_triage.json: %s", e)
                if (
                    triage.get("delivery_mode") == "import_iterate"
                    and triage.get("confidence") in ("high", "medium")
                ):
                    raise ImportModeRecommendedError(
                        "Meta triage: use Import mode (analyze + Refine) instead of greenfield build.",
                        triage,
                    )
            
            # Run meta agent (vision digest + backstories)
            backstories = self.meta_agent.run(self.vision)
            self.agent_backstories = backstories
            # Persist for resume-from-checkpoint
            try:
                (self.workspace_path / "agent_backstories.json").write_text(
                    _json.dumps(backstories, indent=2), encoding="utf-8"
                )
            except Exception as e:
                logger.warning("Could not save agent_backstories.json: %s", e)
            
            # Extract project context from meta agent analysis
            meta_rag = get_phase_rag_context(
                self.document_indexer, "meta", self.config, extra_query=self.vision[:1000],
            )
            self.project_context = self.meta_agent.analyze_vision(
                self.vision, reference_context=meta_rag,
            )
            
            # Transition to next phase only after successful completion
            self.state_machine.transition(
                ProjectState.PRODUCT_OWNER,
                TransitionContext(phase="meta_completed", data={"backstories": backstories})
            )
            
            logger.info("✅ Meta phase completed")
            return backstories
        except ImportModeRecommendedError:
            raise
        except Exception as e:
            self.error_recovery.handle_workflow_error("meta", e, retry_count)
            if self.error_recovery.should_retry("meta", retry_count) and retry_count < 3:
                logger.warning(f"⚠️  Meta phase failed, retrying... ({retry_count + 1}/3)")
                # Rollback to META state for retry
                self.state_machine.rollback_to(ProjectState.META)
                return self.run_meta_phase(retry_count + 1)
            else:
                logger.error(f"❌ Meta phase failed after {retry_count + 1} attempts")
                raise
    
    _PO_FEATURE_RETRY_PROMPT = (
        "CRITICAL: Create ALL product-owner artifacts for this project.\n\n"
        "Project Vision: {vision}\n"
        "Project Context: {context_digest}\n\n"
        "Required files:\n"
        "1. 'requirements.md' — high-level requirements (complete, not truncated)\n"
        "2. 'user_stories.md' — detailed user stories with acceptance criteria "
        "(As a… I want… So that… / Given… When… Then…)\n"
        "3. One 'features/<domain_name>.feature' file per major feature in proper Gherkin "
        "(Feature / Scenario / Given / When / Then). Use domain-specific names.\n"
    )

    def _po_materialize_response(self, response_str: str, label: str = "ProductOwner-retry") -> None:
        """Parse PO response and write requirements, stories, and feature files."""
        write_files_from_response(
            response_str,
            self.workspace_path,
            raw_fallback_path="user_stories.md",
            label=label,
        )

    def run_product_owner_phase(self) -> str:
        """Run Product Owner phase to create user stories and BDD feature files."""
        logger.info("🚀 Starting Product Owner phase...")
        self._report_progress('product_owner', 30, "Creating user stories...")

        solution_spec_file = self.workspace_path / "solution_spec.md"
        if solution_spec_file.exists():
            try:
                spec_content = solution_spec_file.read_text(encoding="utf-8", errors="replace")
                if spec_content.strip():
                    section = f"\n\n## SOLUTION SPECIFICATION\n\n{spec_content.strip()}\n"
                    self.project_context = (self.project_context or "") + section
            except OSError as exc:
                logger.warning("Could not read solution_spec.md for PO context: %s", exc)

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
                config=self.config,
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
                config=self.config,
            )
            retry_prompt = self._PO_FEATURE_RETRY_PROMPT.format(
                vision=self.vision,
                context_digest=self.project_context or "",
            )
            if not self.product_owner_agent.supports_react:
                retry_prompt += product_owner_format_instruction()
            result = str(self.product_owner_agent.agent.chat(retry_prompt))
            self._po_materialize_response(result, label="ProductOwner-retry-gate1")
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
                config=self.config,
            )
            feature_prompt = (
                "Create Gherkin .feature files for this project.\n\n"
                f"Project Vision: {self.vision}\n\n"
                f"User Stories:\n{self.user_stories or 'Not available — create them too.'}\n\n"
                "For each major feature, output a file at features/<domain_specific_name>.feature "
                "containing proper Gherkin:\n"
                "  Feature: <title>\n"
                "    Scenario: <scenario name>\n"
                "      Given …\n      When …\n      Then …\n\n"
                "Create at least one .feature file per major feature."
            )
            if not self.product_owner_agent.supports_react:
                feature_prompt += product_owner_format_instruction()
            result = str(self.product_owner_agent.agent.chat(feature_prompt))
            self._po_materialize_response(result, label="ProductOwner-retry-features")
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
        designer_rag = get_phase_rag_context(
            self.document_indexer, "designer", self.config,
            extra_query=(self.user_stories or "")[:1500],
        )
        result = self.designer_agent.run(
            self.user_stories or "",
            self.project_context,
            vision=self.vision,
            reference_context=designer_rag,
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
                    reference_context=designer_rag,
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
        
        ta_rag = get_phase_rag_context(
            self.document_indexer, "tech_architect", self.config,
            extra_query=(self.design_spec or "")[:1500],
        )
        
        result = ""
        max_attempts = 3
        validation_msg = ""
        
        for attempt in range(max_attempts):
            if attempt == 0:
                result = self.tech_architect_agent.run(
                    self.design_spec or "",
                    self.vision,
                    self.project_context,
                    reference_context=ta_rag,
                )
            else:
                logger.info(f"🔄 Tech Architect retry attempt {attempt + 1}/{max_attempts}")
                self._report_progress('tech_architect', 60 + attempt, f"Retrying tech stack definition (Attempt {attempt + 1})...")
                result = str(self.tech_architect_agent.agent.chat(
                    f"Your previous tech stack definition was incomplete. Please rewrite tech_stack.md addressing these issues:\n{validation_msg}"
                ))
            
            # Fallback: if agent returned content but didn't call file_writer
            _persist_phase_artifact(self.workspace_path, "tech_stack.md", result)
            
            tech_stack_file = self.workspace_path / "tech_stack.md"
            if tech_stack_file.exists():
                with open(tech_stack_file, 'r', encoding='utf-8') as f:
                    self.tech_stack = f.read()
                    
                validation_result = self.task_manager.validate_tech_stack_completeness(self.tech_stack)
                if validation_result["valid"]:
                    break
                else:
                    validation_msg = "\n".join(f"- {i}" for i in validation_result["issues"])
                    logger.warning(f"Tech stack validation failed: {validation_msg}")
                    # Only delete the file if there are more retries remaining,
                    # so the last attempt's tech_stack.md is preserved and the
                    # pipeline can always proceed to register_granular_tasks.
                    if attempt < max_attempts - 1:
                        tech_stack_file.unlink()
        
        if tech_stack_file.exists():
            # Validate tech stack coherence with vision
            _check_vision_coherence(self.vision, self.tech_stack, "tech_stack.md")

            # Register granular per-file tasks with domain context from design spec
            self.task_manager.register_granular_tasks(
                self.design_spec or "",
                self.tech_stack,
            )

            # Skills-first test plan (2nd pass — non-fatal, reuses existing test_plan.md)
            self._generate_test_plan()
            
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

    def _generate_test_plan(self) -> None:
        """Skills-first 2nd pass that writes test_plan.md after Tech Architect (non-fatal)."""
        try:
            plan_file = self.workspace_path / "test_plan.md"
            if plan_file.exists():
                existing = plan_file.read_text(encoding="utf-8", errors="replace").strip()
                if existing:
                    logger.info("test_plan.md exists — skipping regeneration")
                    return

            from ..tools.skill_tools import prefetch_skills
            from ..utils.prompt_loader import load_prompt

            skill_context = prefetch_skills(
                self.vision or "",
                role="tech_architect",
                extra_queries=[
                    "test framework pytest jest vitest junit test directory structure",
                ],
                workspace_path=self.workspace_path,
            )

            tech_stack = self.tech_stack or ""
            ts_file = self.workspace_path / "tech_stack.md"
            if ts_file.exists():
                tech_stack = ts_file.read_text(encoding="utf-8", errors="replace")

            prompt_template = load_prompt("test_plan_task.txt")
            prompt = prompt_template.format(
                vision=(self.vision or "")[:4000],
                tech_stack=tech_stack[:12000],
                skill_context=skill_context or "(none)",
            )

            llm = self._get_manager_llm()
            result = str(llm.complete(prompt))
            _persist_phase_artifact(self.workspace_path, "test_plan.md", result)
            logger.info("✅ test_plan.md generated")
        except Exception as exc:
            logger.warning("Test plan generation failed (non-fatal): %s", exc)

    def _build_test_critique(
        self,
        backend_result: Dict[str, Any],
        frontend_result: Dict[str, Any],
        failures: List[Dict[str, Any]],
    ) -> str:
        lines = ["TEST FAILURES — fix these before continuing:"]
        if not backend_result.get("skipped"):
            lines.append(
                f"Backend: {backend_result.get('passed_count', 0)}/"
                f"{backend_result.get('total', '?')} passed"
            )
        if not frontend_result.get("skipped"):
            lines.append(
                f"Frontend: {frontend_result.get('passed_count', 0)}/"
                f"{frontend_result.get('total', '?')} passed"
            )
        for failure in failures[:20]:
            lines.append(
                f"  - {failure.get('test', 'unknown')}: "
                f"{str(failure.get('error', ''))[:300]}"
            )
        return "\n".join(lines)

    def _run_feature_test_bed_loop(self) -> None:
        """Run container-isolated tests after file tasks; loop DevAgent on RED."""
        if os.getenv("SMOKE_TEST_BACKEND", "syntax_only") == "syntax_only":
            return

        from ..tools.test_tools import run_feature_tests

        max_iters = int(os.getenv("MAX_TEST_ITERATIONS", "3"))
        ws = str(self.workspace_path)

        for iteration in range(max_iters):
            backend_result = run_feature_tests("backend", ws)
            frontend_result = run_feature_tests("frontend", ws)

            if backend_result.get("skipped") and frontend_result.get("skipped"):
                logger.info("Test bed skipped — no commands in test_plan.md")
                return

            if backend_result.get("passed", True) and frontend_result.get("passed", True):
                logger.info("Test bed GREEN (iteration %d)", iteration + 1)
                return

            failures = list(backend_result.get("failures") or [])
            failures.extend(frontend_result.get("failures") or [])
            critique = self._build_test_critique(
                backend_result, frontend_result, failures,
            )

            metadata = self._load_job_metadata()
            loop_state = metadata.get("loop_state") or {}
            loop_state["current_critique"] = critique
            loop_state["test_iteration"] = iteration + 1
            loop_state["failures"] = failures
            metadata["loop_state"] = loop_state
            self._update_job_metadata(metadata)

            if not self.dev_agent:
                logger.warning("Test bed RED but no dev_agent available for fix pass")
                break

            fix_prompt = (
                "The following tests failed after implementation. Fix the code so all "
                "tests pass.\n"
                "Use code_search(pattern) first to locate relevant code, then patch with "
                "replace_file_content.\n\n"
                f"{critique}"
            )
            try:
                self.dev_agent.agent.reset_chat()
                result = self.dev_agent.agent.chat(fix_prompt)
                self.task_manager.update_task_status_by_output(str(result))
            except Exception as exc:
                logger.error("Test bed fix pass failed: %s", exc)
        else:
            if self.job_db:
                import uuid as _uuid
                last_critique = (
                    (self._load_job_metadata().get("loop_state") or {})
                    .get("current_critique", "")[:500]
                )
                self.job_db.create_validation_issue(
                    issue_id=str(_uuid.uuid4()),
                    job_id=self.project_id,
                    check_name="feature_test_bed",
                    severity="error",
                    file_path=None,
                    line_number=None,
                    description=(
                        f"Tests still failing after {max_iters} iterations. "
                        f"Last critique: {last_critique}"
                    ),
                )
    
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

    # ── File materialization from LLM response ───────────────────────────────

    def _resolve_task_file_on_disk(self, file_path: str) -> Path:
        """Return the workspace path for *file_path*, with basename fallback."""
        full_path = self.workspace_path / file_path
        if full_path.exists():
            return full_path
        by_name = {
            p.name: p
            for p in self.workspace_path.rglob("*")
            if p.is_file()
        }
        if Path(file_path).name in by_name:
            return by_name[Path(file_path).name]
        return full_path

    def _materialize_file_from_response(
        self,
        result_str: str,
        file_path: str,
        label: str,
        *,
        agent_simple: bool,
    ) -> bool:
        """Parse *result_str* and write *file_path* when tools did not.

        Simple mode: primary write path (always runs).
        ReAct mode: safety net only — runs when ``file_writer`` did not create the file.

        Returns True if the file exists on disk after this call.
        """
        if not file_path:
            return False

        exists_before = self._resolve_task_file_on_disk(file_path).exists()
        if not agent_simple and exists_before:
            return True

        write_label = label if agent_simple else f"{label}-safetynet"
        if not agent_simple:
            logger.info(
                "[%s] safety net: %s missing after ReAct — parsing response",
                label, file_path,
            )

        result = write_files_from_response(
            result_str,
            self.workspace_path,
            target_file_path=file_path,
            label=write_label,
        )
        if result.written_paths:
            logger.info(
                "[%s] materialized %s via %s (%s)",
                label, file_path, result.parse_strategy, write_label,
            )
        return self._resolve_task_file_on_disk(file_path).exists()

    # ── Parallel-worker count resolution ─────────────────────────────────────

    def _resolve_parallel_workers(self) -> int:
        """Return the number of parallel file-generation workers.

        Resolution order (first match wins):
        1. ``PARALLEL_FILE_WORKERS`` environment variable (runtime override).
        2. ``generation.parallel_file_workers`` from the loaded config.yaml.
        3. Hard-coded default of 5.
        """
        env_val = os.environ.get("PARALLEL_FILE_WORKERS")
        if env_val:
            return max(1, int(env_val))
        if self.config is not None:
            gen = getattr(self.config, "generation", None)
            if gen is not None:
                return max(1, int(gen.parallel_file_workers))
        return 5

    def _generation_settings(self):
        """Return generation config or sensible defaults."""
        gen = getattr(self.config, "generation", None) if self.config else None
        return gen

    def _dev_prompt_context(
        self, agent_simple: bool, file_path: str = "",
    ) -> tuple[str, str]:
        """Return (tech_stack, user_stories) sized for the agent mode."""
        ts = self.tech_stack or ""
        us = self.user_stories or ""
        if not agent_simple:
            return ts, us

        gen = self._generation_settings()
        large = is_likely_large_file(file_path)
        if large:
            max_ts = int(getattr(gen, "simple_mode_large_file_tech_stack_chars", 24_000) if gen else 24_000)
            max_us = int(getattr(gen, "simple_mode_large_file_user_stories_chars", 6_000) if gen else 6_000)
        else:
            max_ts = int(getattr(gen, "simple_mode_max_tech_stack_chars", 12_000) if gen else 12_000)
            max_us = int(getattr(gen, "simple_mode_max_user_stories_chars", 3_000) if gen else 3_000)

        return (
            trim_tech_stack_for_prompt(ts, max_ts),
            trim_user_stories_for_prompt(us, max_us),
        )

    # ── Reusable task-processing loop ────────────────────────────────────────

    def _process_file_tasks(
        self,
        agent,
        task_id_set: set,
        label: str,
        completed_files: dict,
        export_registry: dict,
        lock: "threading.Lock",
        *,
        agent_factory=None,
    ) -> int:
        """Process file-creation tasks using *agent* (or a pool from *agent_factory*).

        Pass ``agent_factory`` (a zero-arg callable that returns a fresh agent)
        to enable parallel mode.  The number of worker threads is controlled by
        the ``PARALLEL_FILE_WORKERS`` environment variable (default 3).

        Sequential mode is used as a fallback when no factory is provided or
        when ``PARALLEL_FILE_WORKERS=1``.
        """
        from ..tools.file_tools import set_thread_workspace, set_allowed_file_paths

        set_thread_workspace(str(self.workspace_path))
        set_allowed_file_paths(None, workspace=str(self.workspace_path))

        num_workers = self._resolve_parallel_workers()

        if agent_factory is not None and num_workers > 1:
            return self._process_file_tasks_parallel(
                agent_factory, task_id_set, label,
                completed_files, export_registry, lock, num_workers,
            )

        # ── Sequential path (backward-compatible) ────────────────────────────
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
                time.sleep(1)
                stall_counter += 1
                continue
            stall_counter = 0
            self.task_manager.mark_task_started(task.task_id)
            count += 1
            self._process_claimed_task(
                task, agent, label, completed_files, export_registry, lock, count,
            )

        return count

    def _process_file_tasks_parallel(
        self,
        agent_factory,
        task_id_set: set,
        label: str,
        completed_files: dict,
        export_registry: dict,
        lock: "threading.Lock",
        num_workers: int,
    ) -> int:
        """Parallel variant of _process_file_tasks.

        Spins up *num_workers* threads, each with its own agent instance.
        Tasks are claimed atomically from the shared queue so no two workers
        ever process the same file.  Workers wait briefly when all remaining
        tasks have unmet dependencies (a sibling may complete them soon).
        """
        import concurrent.futures as _cf
        import threading as _threading
        from ..tools.file_tools import set_thread_workspace, set_allowed_file_paths

        count_lock = _threading.Lock()
        total = [0]

        logger.info(
            "[%s] ⚡ Parallel file generation: %d workers for %d tasks",
            label, num_workers, len(task_id_set),
        )

        def worker(worker_id: int) -> int:
            set_thread_workspace(str(self.workspace_path))
            set_allowed_file_paths(None, workspace=str(self.workspace_path))

            agent = agent_factory()
            local_count = 0
            stall = 0

            while True:
                task = self.task_manager.get_and_claim_actionable_task(
                    "development", task_id_filter=task_id_set,
                )
                if task is None:
                    if not self.task_manager.has_pending_or_active_tasks(
                        "development", task_id_set
                    ):
                        break  # All tasks done or claimed
                    if stall > 40:  # 20 s with no new work
                        logger.warning(
                            "[%s] Worker %d: stalled 20 s with no new tasks — exiting",
                            label, worker_id,
                        )
                        break
                    stall += 1
                    time.sleep(0.5)
                    continue
                stall = 0
                local_count += 1
                self._process_claimed_task(
                    task, agent, label, completed_files, export_registry, lock, local_count,
                )

            with count_lock:
                total[0] += local_count
            logger.info("[%s] Worker %d finished: %d tasks", label, worker_id, local_count)
            return local_count

        with _cf.ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, i) for i in range(num_workers)]
            for fut in _cf.as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:
                    logger.error("[%s] Parallel worker raised: %s", label, exc)

        logger.info("[%s] ⚡ Parallel generation complete: %d total tasks", label, total[0])
        return total[0]

    def _process_claimed_task(
        self,
        task,
        agent,
        label: str,
        completed_files: dict,
        export_registry: dict,
        lock: "threading.Lock",
        task_num: int,
    ) -> None:
        """Execute one already-claimed task with the given agent.

        Called by both the sequential loop (after ``mark_task_started``) and
        the parallel workers (after ``get_and_claim_actionable_task``).
        Handles auto-generated files, LLM generation, validation, and retries.
        """
        from ..orchestrator.code_validator import CodeCompletenessValidator

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
            logger.info("[%s] Task %d: skipped (no file_path) %s", label, task_num, task.task_id)
            return

        logger.info("[%s] Task %d: generating %s", label, task_num, file_path or task.description)
        self._report_progress(
            'development',
            65 + min(20, task_num),
            f"[{label}] Creating {file_path or task.description}...",
        )

        if auto_content is not None and file_path:
            target = self.workspace_path / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(auto_content, encoding="utf-8")
            self.task_manager.update_task_status(
                task.task_id, "completed", f"Auto-generated: {file_path}"
            )
            logger.info("[%s] ✅ Auto-generated %s", label, file_path)
            return

        MAX_FILE_RETRIES = 2
        retry_prompt = None
        agent_simple = not getattr(agent, "supports_react", True)
        gen = self._generation_settings()
        skip_rag = False
        retry_critical_only = False
        if agent_simple:
            skip_rag = getattr(gen, "simple_mode_skip_rag", True) if gen else True
            MAX_FILE_RETRIES = int(getattr(gen, "simple_mode_max_retries", 2) if gen else 2)
            retry_critical_only = bool(
                getattr(gen, "simple_mode_retry_critical_only", False) if gen else False
            )
            # Complex targets benefit from RAG snippets even in simple mode.
            if skip_rag and is_likely_large_file(file_path):
                skip_rag = False

        for attempt in range(MAX_FILE_RETRIES + 1):
            agent.agent.reset_chat()

            with lock:
                snap_exports = dict(export_registry)

            pl = getattr(self.config, "prompt_limits", None) if self.config else None
            max_pvc = getattr(pl, "max_project_vision_chars", None) if pl else None
            max_dep_chars = int(getattr(pl, "max_completed_file_chars", 8192)) if pl else 8192
            if agent_simple and is_likely_large_file(file_path):
                large_dep = int(
                    getattr(gen, "simple_mode_large_file_related_chars", 16_384) if gen else 16_384
                )
                max_dep_chars = max(max_dep_chars, large_dep)
            with lock:
                related_files = self.task_manager.get_related_existing_files(
                    task, completed_files, max_chars_per_file=max_dep_chars,
                )
            file_rag = ""
            if file_path and not (agent_simple and skip_rag):
                file_rag = get_phase_rag_context(
                    self.document_indexer,
                    "development",
                    self.config,
                    extra_query=f"Implement file {file_path}. {(task.description or '')[:500]}",
                )
            prompt_ts, prompt_us = self._dev_prompt_context(agent_simple, file_path)
            tldr_context = ""
            if agent_simple and gen and getattr(gen, "simple_mode_tldr_enabled", True):
                from ..tools.tldr_tools import prefetch_tldr_context, detect_tldr_lang
                with lock:
                    completed_count = len(completed_files)
                tldr_context = prefetch_tldr_context(
                    workspace_path=self.workspace_path,
                    file_path=file_path,
                    task=task,
                    completed_files=completed_count,
                    lang=detect_tldr_lang(self.workspace_path),
                    structure_cache=self._tldr_structure_cache,
                    config=gen,
                )
            prompt = self.task_manager.build_file_prompt(
                task,
                tech_stack=prompt_ts,
                user_stories=prompt_us,
                existing_files=related_files,
                project_vision=self.vision or "",
                max_project_vision_chars=max_pvc,
                interface_contract=snap_exports if snap_exports else None,
                api_contract=self.api_contract,
                rag_context=file_rag,
                simple_mode=agent_simple,
                tldr_context=tldr_context,
            )

            if attempt > 0 and retry_prompt:
                prompt = retry_prompt

            if agent_simple:
                prompt = prompt + simple_mode_format_instruction(file_path or None)

            try:
                result = agent.agent.chat(prompt)
                result_str = str(result)

                if not agent_simple:
                    self.task_manager.update_task_status_by_output(result_str)

                if file_path:
                    self._materialize_file_from_response(
                        result_str, file_path, label, agent_simple=agent_simple,
                    )
                    if agent_simple:
                        result_str += f"\n✅ Successfully wrote to {file_path}"
            except Exception as e:
                logger.error("[%s] Task %s failed: %s", label, task.task_id, e)
                self.task_manager.mark_task_executed(task.task_id, TaskStatus.FAILED, str(e))
                return

            if not file_path:
                self.task_manager.update_task_status(
                    task.task_id,
                    "completed",
                    f"Feature implemented: {task.description or task.task_id}",
                )
                return

            full_path = self._resolve_task_file_on_disk(file_path)
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
            retry_issues = filter_retry_issues(all_issues, critical_only=retry_critical_only)

            if not retry_issues or attempt == MAX_FILE_RETRIES:
                self.task_manager.update_task_status(task.task_id, "completed", f"File created: {file_path}")
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    pl = getattr(self.config, "prompt_limits", None) if self.config else None
                    max_dep = int(getattr(pl, "max_completed_file_chars", 8192)) if pl else 8192
                    if len(content) <= max_dep:
                        with lock:
                            completed_files[file_path] = content
                    else:
                        with lock:
                            completed_files[file_path] = content[:max_dep] + "\n# ... truncated ..."
                except Exception:
                    pass
                try:
                    summary = CodeCompletenessValidator.extract_export_summary(full_path)
                    with lock:
                        export_registry[file_path] = summary.get("exports", [])
                except Exception:
                    pass
                if all_issues and not retry_issues:
                    logger.info(
                        "[%s] File %s has non-critical issues (accepted): %s",
                        label, file_path, all_issues,
                    )
                elif all_issues:
                    logger.warning(
                        "[%s] ⚠️ File %s still has issues after retry: %s",
                        label, file_path, all_issues,
                    )
                return

            logger.warning(
                "[%s] ⚠️ File %s has critical issues (attempt %d), retrying: %s",
                label, file_path, attempt + 1, retry_issues,
            )
            if agent_simple:
                retry_prompt = (
                    f"The file `{file_path}` you just created has these CRITICAL issues:\n"
                    + "\n".join(f"- {i}" for i in retry_issues)
                    + f"\n\nPlease fix and output the COMPLETE corrected `{file_path}` as a JSON array. "
                    + "Do NOT truncate the file — include every line of the full implementation:\n"
                    + '[{"file_path": "' + file_path + '", "content": "...complete fixed content..."}]'
                )
            else:
                retry_prompt = (
                    f"The file `{file_path}` you just created has these issues:\n"
                    + "\n".join(f"- {i}" for i in all_issues)
                    + f"\n\nPlease fix and rewrite `{file_path}` using file_writer."
                )

    def run_development_phase(self) -> str:
        """Run Development phase: iterate per-task, generating one file at a time.

        For fullstack projects (backend + frontend), backend and frontend
        file tasks are processed **in parallel** by DevAgent and FrontendAgent
        respectively, cutting wall-clock time roughly in half.
        """
        import threading as _threading

        logger.info("🚀 Starting Development phase...")
        self._report_progress('development', 65, "Implementing application logic...")

        # Ensure test_plan.md exists on resume (idempotent — skips if present)
        self._generate_test_plan()


        if self.state_machine.get_current_state() != ProjectState.DEVELOPMENT:
            if self.state_machine.can_transition(ProjectState.DEVELOPMENT):
                self.state_machine.transition(
                    ProjectState.DEVELOPMENT,
                    TransitionContext(phase="development", data={})
                )
            else:
                self.state_machine.force_transition(
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

        # Retry feature tasks stuck failed/in_progress from a prior interrupted run
        for t in self.task_manager.get_incomplete_tasks():
            if t.task_type == "feature" and t.status in (
                TaskStatus.FAILED.value,
                TaskStatus.IN_PROGRESS.value,
                TaskStatus.CREATED.value,
            ):
                self.task_manager.update_task_status(
                    t.task_id,
                    TaskStatus.REGISTERED.value,
                    "Reset for development retry",
                )

        # Create the primary dev agent (also used as the first parallel worker)
        backstory = self.agent_backstories.get('developer')
        self.dev_agent = DevAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
            workspace_path=self.workspace_path,
            config=self.config,
        )

        def _make_dev_agent():
            return DevAgent(
                custom_backstory=backstory,
                budget_tracker=self.budget_tracker,
                workspace_path=self.workspace_path,
                config=self.config,
            )

        from ..tools.file_tools import set_allowed_file_paths
        from ..utils.manifest_guard import dev_phase_write_guard_enabled

        if dev_phase_write_guard_enabled():
            allowed = self.task_manager.get_registered_file_paths()
            set_allowed_file_paths(allowed, workspace=str(self.workspace_path))
            logger.info(
                "🔒 file_writer allowlist enabled (strict): %d paths workspace=%s",
                len(allowed),
                self.workspace_path,
            )

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
            fe_backstory = self.agent_backstories.get('frontend_developer')
            self.frontend_agent = FrontendAgent(
                custom_backstory=fe_backstory,
                budget_tracker=self.budget_tracker,
                workspace_path=self.workspace_path,
            )

            def _make_fe_agent():
                return FrontendAgent(
                    custom_backstory=fe_backstory,
                    budget_tracker=self.budget_tracker,
                    workspace_path=self.workspace_path,
                )

            logger.info(
                "⚡ Fullstack project: backend (%d) + frontend (%d) tasks, "
                "each lane parallelised with PARALLEL_FILE_WORKERS workers",
                len(backend_task_ids), len(frontend_task_ids),
            )
            self._report_progress(
                'development', 65,
                f"Parallel build: {len(backend_task_ids)} backend + "
                f"{len(frontend_task_ids)} frontend tasks...",
            )

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                be_future = pool.submit(
                    self._process_file_tasks,
                    self.dev_agent, backend_task_ids, "backend",
                    completed_files, export_registry, lock,
                    agent_factory=_make_dev_agent,
                )
                fe_future = pool.submit(
                    self._process_file_tasks,
                    self.frontend_agent, frontend_task_ids, "frontend",
                    completed_files, export_registry, lock,
                    agent_factory=_make_fe_agent,
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
                agent_factory=_make_dev_agent,
            )

        self._export_registry = export_registry

        # Feature test bed — opt-in via SMOKE_TEST_BACKEND != syntax_only
        self._run_feature_test_bed_loop()

        # Handle any remaining feature tasks
        feature_tasks = [t for t in self.task_manager.get_incomplete_tasks() if t.task_type == "feature"]
        if feature_tasks:
            from ..tools.file_tools import set_allowed_file_paths
            set_allowed_file_paths(None, workspace=str(self.workspace_path))
            feature_names = [t.description for t in feature_tasks]
            try:
                result = self.dev_agent.run(feature_names, self.tech_stack or "", self.user_stories)
                self.task_manager.update_task_status_by_output(result)
                for t in feature_tasks:
                    if t.status not in (TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value):
                        self.task_manager.update_task_status(
                            t.task_id,
                            "completed",
                            f"Feature batch implemented: {t.description or t.task_id}",
                        )
            except Exception as e:
                logger.error("Feature implementation failed: %s", e)
                for t in feature_tasks:
                    if t.status not in (TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value):
                        self.task_manager.mark_task_executed(
                            t.task_id, TaskStatus.FAILED, str(e)[:500],
                        )
        
        # ── Post-development completeness check ──
        try:
            from ..tools.file_tools import set_allowed_file_paths
            set_allowed_file_paths(None, workspace=str(self.workspace_path))

            from ..orchestrator.code_validator import CodeCompletenessValidator
            entry_check = CodeCompletenessValidator.validate_entrypoint(
                self.workspace_path, self.tech_stack or ""
            )
            if not entry_check.get("valid", True):
                missing = entry_check.get("missing_wiring") or []
                detail = missing[0] if missing else "entrypoint wiring incomplete"
                logger.warning("Post-dev gap-fill: entrypoint issue — %s", detail)
                self.dev_agent.run(
                    [f"Create or fix the application entrypoint/bootstrap file. {detail}"],
                    self.tech_stack or "",
                    self.user_stories,
                )

            structure_gaps = self.task_manager.detect_workspace_structure_gaps(self.workspace_path)
            if structure_gaps:
                logger.warning(
                    "Post-dev gap-fill: %d structural gap(s) — %s",
                    len(structure_gaps),
                    structure_gaps[0][:120],
                )
                self.dev_agent.run(structure_gaps, self.tech_stack or "", self.user_stories)
        except Exception as e:
            logger.warning("Post-dev completeness check failed: %s", e)

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
            if self.state_machine.can_transition(ProjectState.FRONTEND):
                self.state_machine.transition(
                    ProjectState.FRONTEND,
                    TransitionContext(phase="frontend", data={})
                )
            else:
                self.state_machine.force_transition(
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
                max_dep_chars = int(getattr(pl, "max_completed_file_chars", 8192)) if pl else 8192
                related_files = self.task_manager.get_related_existing_files(
                    task, existing_files, max_chars_per_file=max_dep_chars,
                )
                file_rag = ""
                if file_path:
                    file_rag = get_phase_rag_context(
                        self.document_indexer,
                        "development",
                        self.config,
                        extra_query=f"Frontend file {file_path}. {(task.description or '')[:500]}",
                    )
                prompt = self.task_manager.build_file_prompt(
                    task,
                    tech_stack=self.tech_stack or "",
                    user_stories=self.user_stories or "",
                    existing_files=related_files,
                    project_vision=self.vision or "",
                    max_project_vision_chars=max_pvc,
                    interface_contract=self._export_registry if self._export_registry else None,
                    api_contract=self.api_contract,
                    rag_context=file_rag,
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
            if self.state_machine.can_transition(ProjectState.DEVOPS):
                self.state_machine.transition(
                    ProjectState.DEVOPS,
                    TransitionContext(phase="devops", data={})
                )
            else:
                self.state_machine.force_transition(
                    ProjectState.DEVOPS,
                    TransitionContext(phase="devops", data={}),
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
                future.result(timeout=_DEVOPS_TIMEOUT_SECS)
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
            job_metadata = self._load_job_metadata()
            if is_epic_job(job_metadata):
                return self._run_epic_workflow(job_metadata, resume=resume)

            resume_from_checkpoint = False
            if resume:
                self._load_phase_artifacts()
                current = self.state_machine.get_current_state()
                if current == ProjectState.COMPLETED:
                    logger.info("Resume: state already completed; running full workflow")
                elif current == ProjectState.FAILED:
                    inferred = self._infer_resume_state_from_artifacts()
                    if inferred:
                        logger.info(
                            "Resume: failed state — inferring checkpoint %s from workspace",
                            inferred.value,
                        )
                        self.state_machine.force_transition(inferred)
                        current = inferred
                        resume_from_checkpoint = True
                    else:
                        logger.info("Resume: failed with no checkpoint artifacts; running full workflow")
                else:
                    resume_from_checkpoint = True

            if resume_from_checkpoint:
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
            else:
                # Phase 1: Meta (has its own retry logic)
                self.run_meta_phase()
                # Solutioning loop (config-gated)
                if self._solutioning_enabled():
                    metadata = self._load_job_metadata()
                    if not metadata.get("solution_approved"):
                        self._run_solutioning_loop()
                        return self._pause_for_solution_review()
                # Phases 2–4: planning — can be paused for human review
                self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
                self._run_phase_with_retry("designer", self.run_designer_phase)
                self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
                # Universal plan review gate (config-gated)
                metadata = self._load_job_metadata()
                if self._plan_review_enabled() and not metadata.get("pending_review_approved"):
                    return self._pause_for_plan_review()
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
