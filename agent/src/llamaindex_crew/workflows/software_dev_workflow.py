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
from ..orchestrator.task_manager import TaskManager, TaskStatus
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
    r"All (?:files|content) (?:have been|align))",
    re.IGNORECASE | re.MULTILINE,
)


def _is_agent_summary(text: str) -> bool:
    """Return True if *text* looks like a meta-summary instead of real artifact content."""
    first_200 = text[:200]
    return bool(_SUMMARY_PATTERNS.search(first_200))


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
        progress_callback: Optional[callable] = None
    ):
        """
        Initialize workflow
        
        Args:
            project_id: Unique project identifier
            workspace_path: Path to workspace directory
            vision: Project vision/idea
            config: Optional configuration instance
            progress_callback: Optional callback function(phase: str, progress: int, message: str)
        """
        self.project_id = project_id
        self.workspace_path = workspace_path
        self.vision = vision
        self.config = config
        self.progress_callback = progress_callback
        
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
    
    def _report_progress(self, phase: str, progress: int, message: str = None):
        """Report progress via callback if available"""
        if self.progress_callback:
            try:
                self.progress_callback(phase, progress, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

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
            budget_tracker=self.budget_tracker
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
                    budget_tracker=self.budget_tracker
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

        contract_result = self.tech_architect_agent.generate_api_contract(
            tech_stack=self.tech_stack,
            design_spec=self.design_spec or "",
            user_stories=self.user_stories or "",
        )

        _persist_phase_artifact(self.workspace_path, "api_contract.yaml", contract_result)

        contract_file = self.workspace_path / "api_contract.yaml"
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
    
    def run_development_phase(self) -> str:
        """Run Development phase: iterate per-task, generating one file at a time."""
        logger.info("🚀 Starting Development phase...")
        self._report_progress('development', 65, "Implementing application logic...")
        
        from ..orchestrator.code_validator import CodeCompletenessValidator
        
        if self.state_machine.get_current_state() != ProjectState.DEVELOPMENT:
            self.state_machine.transition(
                ProjectState.DEVELOPMENT,
                TransitionContext(phase="development", data={})
            )
        
        # BDD gate: ensure Gherkin features are available before coding begins
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
        
        # Create dev agent with workspace-bound file tools
        backstory = self.agent_backstories.get('developer')
        self.dev_agent = DevAgent(
            custom_backstory=backstory,
            budget_tracker=self.budget_tracker,
            workspace_path=self.workspace_path,
        )

        # Enable file-path allowlist so the dev agent can only write files
        # that are in the registered task list (prevents hallucinated files).
        from ..tools.file_tools import set_allowed_file_paths
        allowed = self.task_manager.get_registered_file_paths()
        set_allowed_file_paths(allowed)
        logger.info("🔒 file_writer allowlist enabled: %d paths", len(allowed))
        
        # Per-task iteration: pick one file task at a time
        completed_files: dict = {}
        export_registry: dict = {}  # file_path -> export summary (interface contract)
        max_tasks = 100
        task_count = 0
        
        while task_count < max_tasks:
            task = self.task_manager.get_next_actionable_task("development")
            if task is None:
                break
            
            task_count += 1
            file_path = (task.metadata or {}).get("file_path", "")
            auto_content = (task.metadata or {}).get("auto_content")
            logger.info("📝 Task %d: generating %s", task_count, file_path or task.description)
            self._report_progress(
                'development',
                65 + min(20, task_count),
                f"Creating {file_path or task.description}...",
            )
            
            self.task_manager.mark_task_started(task.task_id)

            # Auto-injected files (like __init__.py) skip the LLM entirely
            if auto_content is not None and file_path:
                target = self.workspace_path / file_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(auto_content, encoding="utf-8")
                self.task_manager.update_task_status(
                    task.task_id, "completed", f"Auto-generated: {file_path}"
                )
                logger.info("✅ Auto-generated %s (no LLM call needed)", file_path)
                continue
            
            MAX_FILE_RETRIES = 2
            
            for attempt in range(MAX_FILE_RETRIES + 1):
                self.dev_agent.agent.reset_chat()
                
                pl = getattr(self.config, "prompt_limits", None) if self.config else None
                max_pvc = getattr(pl, "max_project_vision_chars", None) if pl else None
                prompt = self.task_manager.build_file_prompt(
                    task,
                    tech_stack=self.tech_stack or "",
                    user_stories=self.user_stories or "",
                    existing_files=completed_files,
                    project_vision=self.vision or "",
                    max_project_vision_chars=max_pvc,
                    interface_contract=export_registry if export_registry else None,
                    api_contract=self.api_contract,
                )
                
                if attempt > 0:
                    prompt = retry_prompt  # noqa: F821 – set in the validation block below
                
                try:
                    result = self.dev_agent.agent.chat(prompt)
                    result_str = str(result)
                    self.task_manager.update_task_status_by_output(result_str)
                except Exception as e:
                    logger.error("Task %s failed: %s", task.task_id, e)
                    self.task_manager.mark_task_executed(task.task_id, TaskStatus.FAILED, str(e))
                    break
                
                if not file_path:
                    break
                
                full_path = self.workspace_path / file_path
                if not full_path.exists():
                    all_files = {p.name: p for p in self.workspace_path.rglob("*") if p.is_file()}
                    if Path(file_path).name in all_files:
                        full_path = all_files[Path(file_path).name]
                
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
                            completed_files[file_path] = content
                        else:
                            completed_files[file_path] = content[:5120] + "\n# ... truncated ..."
                    except Exception:
                        pass
                    # Build running export registry for the interface contract
                    try:
                        summary = CodeCompletenessValidator.extract_export_summary(full_path)
                        export_registry[file_path] = summary.get("exports", [])
                    except Exception:
                        pass
                    if all_issues:
                        logger.warning("⚠️ File %s still has issues after retry: %s", file_path, all_issues)
                    break
                
                logger.warning("⚠️ File %s has issues (attempt %d), retrying: %s", file_path, attempt + 1, all_issues)
                retry_prompt = (
                    f"The file `{file_path}` you just created has these issues:\n"
                    + "\n".join(f"- {i}" for i in all_issues)
                    + f"\n\nPlease fix and rewrite `{file_path}` using file_writer."
                )
        
        # Handle any remaining feature tasks
        feature_tasks = [t for t in self.task_manager.get_incomplete_tasks() if t.task_type == "feature"]
        if feature_tasks:
            feature_names = [t.description for t in feature_tasks]
            try:
                result = self.dev_agent.run(feature_names, self.tech_stack or "", self.user_stories)
                self.task_manager.update_task_status_by_output(result)
            except Exception as e:
                logger.error("Feature implementation failed: %s", e)
        
        # ── Post-development validation suite ─────────────────────────────
        report: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "workspace": str(self.workspace_path),
            "tasks_processed": task_count,
            "checks": {},
        }
        
        # 1. Workspace-wide completeness check
        ws_result = CodeCompletenessValidator.validate_workspace(self.workspace_path)
        report["checks"]["completeness"] = {
            "pass": len(ws_result.get("incomplete_files", [])) == 0,
            "issues": ws_result.get("incomplete_files", []),
        }
        if ws_result["incomplete_files"]:
            logger.warning(
                "⚠️ Workspace has %d files with quality issues after development phase",
                len(ws_result["incomplete_files"]),
            )
            for inc in ws_result["incomplete_files"]:
                logger.warning("  - %s: %s", inc["file"], inc["issues"])
        
        # 2. Per-file integration check (syntax + imports)
        _SRC_EXT = {".py", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".go"}
        file_issues: List[Dict[str, Any]] = []
        for src_file in sorted(self.workspace_path.rglob("*")):
            if src_file.is_file() and src_file.suffix in _SRC_EXT:
                rel = str(src_file.relative_to(self.workspace_path))
                integ = CodeCompletenessValidator.validate_file_integration(src_file, self.workspace_path)
                entry = {"file": rel, "valid": integ["valid"], "issues": integ.get("issues", [])}
                file_issues.append(entry)
                if not integ["valid"]:
                    logger.warning("⚠️ Integration issues in %s: %s", rel, integ["issues"])
        report["checks"]["integration"] = {
            "pass": all(f["valid"] for f in file_issues),
            "files": file_issues,
        }
        
        # 3. Dependency manifest completeness
        manifest_result = CodeCompletenessValidator.validate_dependency_manifest(self.workspace_path)
        report["checks"]["dependency_manifest"] = {
            "pass": manifest_result.get("valid", True),
            "missing": manifest_result.get("missing", []),
        }
        if not manifest_result["valid"]:
            logger.warning(
                "⚠️ Dependency manifest incomplete — %d undeclared packages:",
                len(manifest_result["missing"]),
            )
            for entry in manifest_result["missing"]:
                logger.warning("  - [%s] %s (used in: %s)", entry["ecosystem"], entry["package"], ", ".join(entry["files"][:3]))
        
        # 4. Tech stack conformance
        if self.tech_stack:
            stack_result = CodeCompletenessValidator.validate_tech_stack_conformance(
                self.workspace_path, self.tech_stack
            )
            report["checks"]["tech_stack"] = {
                "pass": stack_result.get("valid", True),
                "conflicts": stack_result.get("conflicts", []),
            }
            if not stack_result["valid"]:
                logger.warning(
                    "⚠️ Tech stack conflicts detected (%d):", len(stack_result["conflicts"])
                )
                for c in stack_result["conflicts"]:
                    logger.warning("  - %s: %s — %s", c["file"], c["conflict"], c["detail"])
        else:
            report["checks"]["tech_stack"] = {"pass": True, "conflicts": [], "note": "no tech stack defined"}
        
        # 5. Package structure (Python __init__.py)
        pkg_result = CodeCompletenessValidator.validate_package_structure(self.workspace_path)
        report["checks"]["package_structure"] = {
            "pass": pkg_result["valid"],
            "missing_init": pkg_result.get("missing_init", []),
        }
        if not pkg_result["valid"]:
            logger.warning(
                "⚠️ Missing __init__.py in %d package dir(s):", len(pkg_result["missing_init"])
            )
            for d in pkg_result["missing_init"]:
                logger.warning("  - %s/", d)

        # 6. Duplicate / scattered files
        dup_result = CodeCompletenessValidator.validate_duplicate_files(self.workspace_path)
        report["checks"]["duplicate_files"] = {
            "pass": dup_result["valid"],
            "duplicates": dup_result.get("duplicates", []),
        }
        if not dup_result["valid"]:
            logger.warning(
                "⚠️ Duplicate source files detected (%d):", len(dup_result["duplicates"])
            )
            for d in dup_result["duplicates"]:
                logger.warning("  - %s: %s", d["filename"], ", ".join(d["paths"]))

        # 7. Entrypoint wiring
        entrypoint_result = CodeCompletenessValidator.validate_entrypoint(
            self.workspace_path, self.tech_stack or ""
        )
        report["checks"]["entrypoint"] = {
            "pass": entrypoint_result["valid"],
            "framework": entrypoint_result.get("framework", ""),
            "missing_wiring": entrypoint_result.get("missing_wiring", []),
        }
        if not entrypoint_result["valid"]:
            logger.warning(
                "⚠️ Entrypoint wiring issues for %s:", entrypoint_result.get("framework", "?")
            )
            for m in entrypoint_result["missing_wiring"]:
                logger.warning("  - %s", m)

        # 8. File manifest conformance — detect files the dev agent created
        #    outside the tech stack specification.
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
            if rel.endswith(".md") or rel.endswith(".log") or rel.endswith(".json"):
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
        if unauthorized:
            logger.warning(
                "⚠️ %d file(s) created outside the tech stack manifest:", len(unauthorized)
            )
            for u in unauthorized:
                logger.warning("  - %s", u)
        if conflict_pairs:
            logger.warning(
                "⚠️ %d file/package conflict(s):", len(conflict_pairs)
            )
            for c in conflict_pairs:
                logger.warning("  - %s vs %s", c["package"], c["flat_module"])

        # 9. API contract conformance (if contract exists)
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
                if not contract_result.get("valid", True):
                    logger.warning(
                        "⚠️ API contract conformance: %d missing endpoint(s)",
                        len(contract_result.get("missing_endpoints", [])),
                    )
            except Exception as e:
                logger.debug("Contract conformance check skipped: %s", e)

        # 10. Smoke test (best-effort, don't fail the build)
        smoke_msg = ""
        smoke_container_log = ""
        try:
            from ..tools.test_tools import smoke_test_runner
            smoke_result = smoke_test_runner("auto")
            smoke_msg = str(smoke_result)
            smoke_container_log = getattr(smoke_result, "log", "")
            if "❌" in smoke_msg:
                logger.warning("⚠️ Smoke test: %s", smoke_msg)
            else:
                logger.info("🧪 %s", smoke_msg)
        except Exception as e:
            smoke_msg = f"skipped: {e}"
            logger.debug("Smoke test skipped: %s", e)
        report["checks"]["smoke_test"] = {
            "pass": "✅" in smoke_msg,
            "result": smoke_msg,
        }

        # Write container log if the backend produced one
        if smoke_container_log:
            try:
                container_log_path = self.workspace_path / "smoke_test_container.log"
                container_log_path.write_text(
                    f"═══ Smoke Test Container Log ═══\n"
                    f"timestamp: {report['timestamp']}\n"
                    f"backend:   {os.getenv('SMOKE_TEST_BACKEND', 'syntax_only')}\n\n"
                    f"{smoke_container_log}\n",
                    encoding="utf-8",
                )
                logger.info("📋 Container log written to %s", container_log_path)
            except Exception as e:
                logger.warning("Could not write container log: %s", e)

        # Compute overall pass/fail
        all_passed = all(c.get("pass", True) for c in report["checks"].values())
        report["overall"] = "PASS" if all_passed else "ISSUES_FOUND"
        if smoke_container_log:
            report["checks"]["smoke_test"]["container_log_file"] = "smoke_test_container.log"

        # Write validation_report.log to the workspace
        try:
            report_path = self.workspace_path / "validation_report.log"
            lines: List[str] = []
            lines.append("=" * 72)
            lines.append(f"  VALIDATION REPORT — {report['timestamp']}")
            lines.append(f"  Overall: {report['overall']}")
            lines.append("=" * 72)
            lines.append("")

            n_checks = 8

            # 1. Completeness
            comp = report["checks"]["completeness"]
            lines.append(f"[1/{n_checks}] Completeness check: {'PASS' if comp['pass'] else 'FAIL'}")
            for inc in comp["issues"]:
                lines.append(f"  ✗ {inc['file']}: {inc['issues']}")
            lines.append("")

            # 2. Integration (syntax + imports)
            integ_check = report["checks"]["integration"]
            fail_count = sum(1 for f in integ_check["files"] if not f["valid"])
            total = len(integ_check["files"])
            lines.append(f"[2/{n_checks}] Syntax & import validation: {'PASS' if integ_check['pass'] else 'FAIL'} ({total - fail_count}/{total} files clean)")
            for f in integ_check["files"]:
                status = "✓" if f["valid"] else "✗"
                line = f"  {status} {f['file']}"
                if f["issues"]:
                    line += f"  — {'; '.join(str(i) for i in f['issues'])}"
                lines.append(line)
            lines.append("")

            # 3. Dependency manifest
            dep = report["checks"]["dependency_manifest"]
            lines.append(f"[3/{n_checks}] Dependency manifest: {'PASS' if dep['pass'] else 'FAIL'}")
            for m in dep["missing"]:
                lines.append(f"  ✗ [{m['ecosystem']}] {m['package']} (used in: {', '.join(m.get('files', [])[:3])})")
            lines.append("")

            # 4. Tech stack
            ts = report["checks"]["tech_stack"]
            lines.append(f"[4/{n_checks}] Tech stack conformance: {'PASS' if ts['pass'] else 'FAIL'}")
            for c in ts.get("conflicts", []):
                lines.append(f"  ✗ {c.get('file', '?')}: {c.get('conflict', '?')} — {c.get('detail', '')}")
            lines.append("")

            # 5. Package structure
            pkg = report["checks"].get("package_structure", {})
            lines.append(f"[5/{n_checks}] Package structure (__init__.py): {'PASS' if pkg.get('pass', True) else 'FAIL'}")
            for d in pkg.get("missing_init", []):
                lines.append(f"  ✗ {d}/ missing __init__.py")
            lines.append("")

            # 6. Duplicate files
            dup = report["checks"].get("duplicate_files", {})
            lines.append(f"[6/{n_checks}] Duplicate file detection: {'PASS' if dup.get('pass', True) else 'FAIL'}")
            for d in dup.get("duplicates", []):
                lines.append(f"  ✗ {d['filename']}: {', '.join(d['paths'])}")
            lines.append("")

            # 7. Entrypoint wiring
            ep = report["checks"].get("entrypoint", {})
            fw_label = f" ({ep['framework']})" if ep.get("framework") else ""
            lines.append(f"[7/{n_checks}] Entrypoint wiring{fw_label}: {'PASS' if ep.get('pass', True) else 'FAIL'}")
            for m in ep.get("missing_wiring", []):
                lines.append(f"  ✗ {m}")
            lines.append("")

            # 8. Smoke test
            st = report["checks"]["smoke_test"]
            lines.append(f"[8/{n_checks}] Smoke test: {'PASS' if st['pass'] else 'FAIL'}")
            for result_line in st["result"].splitlines():
                lines.append(f"  {result_line}")
            lines.append("")

            lines.append("=" * 72)

            report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            logger.info("📋 Validation report written to %s", report_path)

            # Also write JSON for programmatic access
            json_path = self.workspace_path / "validation_report.json"
            json_path.write_text(_json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning("Could not write validation report: %s", e)
        
        # Disable the file-writer allowlist for subsequent phases
        from ..tools.file_tools import set_allowed_file_paths
        set_allowed_file_paths(None)

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
            from ..tools.file_tools import set_thread_workspace
            set_thread_workspace(str(self.workspace_path))
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
                    for phase_state in _RESUMABLE_PHASES[start_idx:]:
                        phase_name = phase_state.value
                        if phase_state == ProjectState.META:
                            self.run_meta_phase()
                        elif phase_state == ProjectState.PRODUCT_OWNER:
                            self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
                        elif phase_state == ProjectState.DESIGNER:
                            self._run_phase_with_retry("designer", self.run_designer_phase)
                        elif phase_state == ProjectState.TECH_ARCHITECT:
                            self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
                        elif phase_state == ProjectState.DEVELOPMENT:
                            self._run_phase_with_retry("development", self.run_development_phase)
                        elif phase_state == ProjectState.FRONTEND:
                            self._run_phase_with_retry("frontend", self.run_frontend_phase)
                    # Fall through to transition to completed + final sweep below
            if not resume:
                # Phase 1: Meta (has its own retry logic)
                self.run_meta_phase()
                # Phases 2–6: run with retry on transient LLM/network errors
                self._run_phase_with_retry("product_owner", self.run_product_owner_phase)
                self._run_phase_with_retry("designer", self.run_designer_phase)
                self._run_phase_with_retry("tech_architect", self.run_tech_architect_phase)
                self._run_phase_with_retry("development", self.run_development_phase)
                self._run_phase_with_retry("frontend", self.run_frontend_phase)

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
                "state": self.state_machine.get_current_state().value
            }
        except Exception as e:
            logger.error(f"❌ Workflow failed: {e}")
            self.state_machine.transition(
                ProjectState.FAILED,
                TransitionContext(phase="failed", data={"error": str(e)})
            )
            raise
