"""
Main orchestrator for coordinating multiple crews
"""
import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from collections import defaultdict
from rich.console import Console
from rich.panel import Panel

from ..config.settings import settings
from .state_manager import StateManager
from .state_machine import ProjectStateMachine, ErrorRecoveryEngine, ErrorContext, ProjectState, TransitionContext
from ..crews.meta_crew import MetaCrew
from ..crews.product_owner_crew import ProductOwnerCrew
from ..crews.designer_crew import DesignerCrew
from ..crews.tech_architect_crew import TechArchitectCrew
from ..crews.dev_crew import DevCrew
from ..crews.frontend_crew import FrontendCrew
from ..budget.tracker import BudgetTracker, EnhancedBudgetTracker
from ..utils.llm_config import print_llm_config
from ..utils.retry_handler import safe_execute_with_retry
from ..utils.design_specs_loader import load_design_specs, format_specs_for_prompt, get_specs_summary

# Setup logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.WORKSPACE_PATH / "crew_errors.log")
    ]
)
logger = logging.getLogger(__name__)
console = Console()

class SoftwareDevOrchestrator:
    """
    Orchestrates the entire software development process across multiple crews
    """
    
    def __init__(self, progress_callback=None):
        self.budget_tracker = EnhancedBudgetTracker()
        self.state_manager = StateManager()
        self.project_id = settings.PROJECT_ID
        self.activity_log_file = settings.WORKSPACE_PATH / "activity.log"
        self.progress_callback = progress_callback
        self.failure_counts = defaultdict(int)  # Track per-phase failure counts
        
        if (
            not os.getenv("OPENROUTER_API_KEY")
            and not os.getenv("OPENAI_API_KEY")
            and os.getenv("LLM_ENVIRONMENT", "production").lower() != "local"
        ):
            logger.warning("No LLM provider configured. Set LLM_ENVIRONMENT=local or provide API keys (OPENROUTER_API_KEY/OPENAI_API_KEY).")
        
        # Initialize state machine and error recovery
        self.state_machine = ProjectStateMachine(settings.WORKSPACE_PATH, self.project_id)
        self.error_recovery = ErrorRecoveryEngine(self.state_machine)
        
        # Initialize activity log
        with open(self.activity_log_file, 'w') as f:
            f.write(f"{'='*80}\n")
            f.write(f"ACTIVITY LOG - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*80}\n\n")
    
    # ---------------------------------------------------------------------
    # Utility helpers
    # ---------------------------------------------------------------------
    def _require_artifact(self, relative_path: str):
        """Ensure a required artifact exists; raise if missing to surface crew failures."""
        path = settings.WORKSPACE_PATH / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Required artifact missing: {relative_path}")
    
    def _persist_artifact_if_missing(self, relative_path: str, content: Optional[str]):
        """Persist provided content to required artifact if the file is missing.
        
        Skips persistence if the content looks like a status message rather than actual content.
        """
        if content is None:
            return
        path = settings.WORKSPACE_PATH / relative_path
        if path.exists():
            return
        
        # Skip if content looks like a status message or internal reasoning trace
        content_str = str(content).strip()
        content_lower = content_str.lower()
        
        # Detect status messages by patterns
        status_indicators = [
            "files written successfully",
            "successfully wrote",
            "task completed",
            "created successfully",
            "done.",
        ]
        
        # Detect CrewAI reasoning traces (Thought/Action patterns)
        reasoning_indicators = [
            "thought:",
            "action:",
            "action input:",
            "my response should be",
            "to proceed,",
            "the human says:",
        ]
        
        # Also detect summary lists like "✅ Created file1\n✅ Created file2"
        lines = content_str.split('\n')
        status_line_count = sum(1 for line in lines if line.strip().startswith('✅'))
        is_status_list = status_line_count >= 2 and status_line_count == len([l for l in lines if l.strip()])
        
        # Check for reasoning trace patterns
        is_reasoning_trace = any(indicator in content_lower for indicator in reasoning_indicators)
        
        if (any(indicator in content_lower for indicator in status_indicators) and len(content_str) < 500) or is_status_list or is_reasoning_trace:
            self._log_activity(
                f"⚠️  Skipped persisting status message to {relative_path} - agent should write actual content",
                level="WARNING"
            )
            return
        
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(str(content))
            self._log_activity(f"ℹ️ Persisted missing artifact: {relative_path}")
        except Exception as e:
            self._log_activity(f"⚠️  Could not persist artifact {relative_path}: {e}", level="WARNING")
    
    def _ensure_frontend_stub(self):
        """Check if UI files exist; log warning if not (no longer auto-creates)."""
        src_dir = settings.WORKSPACE_PATH / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        
        # Check for any JS/TS/JSX/TSX files under src/
        ui_files = list(src_dir.rglob("*.js")) + list(src_dir.rglob("*.jsx")) + \
                   list(src_dir.rglob("*.ts")) + list(src_dir.rglob("*.tsx"))
        
        if not ui_files:
            self._log_activity("⚠️  WARNING: Frontend crew did not create any UI files under src/", level="WARNING")
    
    def _extract_files_from_tech_stack(self) -> List[str]:
        """
        Parse tech_stack.md to extract the expected file structure.
        Returns a list of file paths that should be created under src/ or __tests__/.
        """
        tech_stack_file = settings.WORKSPACE_PATH / "tech_stack.md"
        if not tech_stack_file.exists():
            return []
        
        try:
            with open(tech_stack_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Look for file structure section
            import re
            
            # Find the "File Structure" section first
            # Match: ## File Structure ... ```[language] ... ``` or ``` ... ```
            file_structure_match = re.search(r'## File Structure.*?```(?:[a-zA-Z]*)?\n([\s\S]*?)```', content, re.IGNORECASE | re.DOTALL)
            if not file_structure_match:
                # Fallback: find any code block that looks like a file tree (has ├── or directory paths)
                code_blocks = re.findall(r'```(?:[a-zA-Z]*)?\n([\s\S]*?)```', content)
                for block in code_blocks:
                    if re.search(r'[├└│─]|^[a-zA-Z0-9_\-]+/', block, re.MULTILINE):
                        file_structure_block = block
                        break
                else:
                    file_structure_block = ""
            else:
                file_structure_block = file_structure_match.group(1)
            
            expected_files = []
            
            # Skip JSON blocks (package.json content, dependencies lists)
            if file_structure_block and (file_structure_block.strip().startswith('{') or '"dependencies"' in file_structure_block):
                # This is a JSON block, not a file structure - skip it
                # Try to find another code block that looks like a file tree
                all_blocks = re.findall(r'```(?:[a-zA-Z]*)?\n([\s\S]*?)```', content)
                for block in all_blocks:
                    # Look for file tree indicators (├──, │, └──, or directory paths)
                    if re.search(r'[├└│─]|^[a-zA-Z0-9_\-]+/', block, re.MULTILINE):
                        file_structure_block = block
                        break
                else:
                    file_structure_block = ""

            # Only process if we have a valid file structure block
            if not file_structure_block:
                self._log_activity("⚠️  Could not find File Structure section in tech_stack.md", level="WARNING")
                return []

            # Look for lines that look like file paths
            lines = file_structure_block.split('\n')
            for line in lines:
                # Match patterns like:
                # ├── src/App.js
                # │   ├── components/Display.js
                # - App.tsx (comment)
                # package.json

                # Skip lines that look like JSON (quoted strings, curly braces)
                if '"' in line or '{' in line or '}' in line or line.strip().startswith('"'):
                    continue
                
                # Skip npm package names (usually have @ prefix or are in dependencies)
                if line.strip().startswith('@') or '/node_modules/' in line:
                    continue
                
                # Extract file paths (must have file extension or be known config files)
                file_match = re.search(r'[├└│─\s]*([a-zA-Z0-9_/\.\-]+(?:\.(?:js|jsx|ts|tsx|py|java|go|rs|cpp|h|c|json|md|config|lock|toml|yml|yaml|gitignore|txt|xml|java|gradle|podfile|xcworkspace))?)', line)
                if file_match:
                    file_path = file_match.group(1).strip()

                    # Skip directory entries (ending with /)
                    if file_path.endswith('/'):
                        continue
                    
                    # Skip npm package names (babel/core, react-native, @types/react, etc.)
                    if file_path.startswith(('@', 'babel/', 'react/', 'types/', 'eslint/', 'jest/', 'prettier/')):
                        continue
                    
                    # Skip if it looks like a package name without a file extension
                    if '/' in file_path and not re.search(r'\.(js|jsx|ts|tsx|json|md|config|lock|toml|yml|yaml|gitignore|txt|xml|java|gradle|podfile|xcworkspace)$', file_path):
                        continue
                    
                    # Normalize path - use as-is from tech_stack.md
                    # Root-level files: package.json, index.js, .gitignore, README.md, etc.
                    # Source files: src/App.js, src/components/Display.js, etc.
                    root_level_files = [
                        'package.json', 'package-lock.json', 'yarn.lock',
                        'index.js', 'index.ts', 'index.tsx',
                        '.gitignore', '.eslintrc.js', '.prettierrc',
                        'babel.config.js', 'metro.config.js', 'app.json',
                        'jest.config.js', 'tsconfig.json', 'eslint.config.js',
                        'README.md', 'Dockerfile', 'docker-compose.yml'
                    ]
                    
                    # Check if file is already a proper path (contains directory separator)
                    if '/' in file_path:
                        expected_files.append(file_path)
                    # Check if file should be at root level
                    elif any(file_path.endswith(root) or file_path == root for root in root_level_files):
                        expected_files.append(file_path)
                    # Skip android/, ios/, node_modules
                    elif file_path.startswith(('android', 'ios', 'node_modules')):
                        continue
                    # Other files likely belong in src/
                    else:
                        expected_files.append(f'src/{file_path}')
            
            # Remove duplicates
            expected_files = list(set(expected_files))
            
            self._log_activity(f"📋 Extracted {len(expected_files)} expected source files from tech_stack.md")
            if expected_files:
                self._log_activity(f"   Expected: {', '.join(expected_files[:5])}{'...' if len(expected_files) > 5 else ''}")
            return expected_files
        except Exception as e:
            self._log_activity(f"⚠️  Could not parse tech_stack.md for file structure: {e}", level="WARNING")
            return []
    
    def _validate_generated_files(self, phase_name: str) -> Dict[str, Any]:
        """
        Validate that all expected files from tech_stack.md were created.
        Returns dict with 'valid', 'missing_files', and 'created_files'.
        """
        expected_files = self._extract_files_from_tech_stack()
        if not expected_files:
            # No file structure found in tech_stack, skip validation
            return {'valid': True, 'missing_files': [], 'created_files': []}
        
        workspace = settings.WORKSPACE_PATH
        created_files = []
        
        # Find all code files actually created
        for ext in ['*.py', '*.js', '*.jsx', '*.ts', '*.tsx', '*.java', '*.go', '*.rs']:
            created_files.extend([str(p.relative_to(workspace)) for p in workspace.rglob(ext)])
        
        # Check for missing files
        missing_files = []
        for expected in expected_files:
            # Check if file exists (handle slight path variations)
            found = False
            expected_lower = expected.lower()
            expected_base = expected.split('/')[-1].lower()
            for created in created_files:
                created_lower = created.lower()
                created_base = created.split('/')[-1].lower()
                if (
                    expected in created
                    or created in expected
                    or expected.endswith(created.split('/')[-1])
                    or expected_lower == created_lower
                    or expected_base == created_base
                ):
                    found = True
                    break
            if not found:
                missing_files.append(expected)
        
        is_valid = len(missing_files) == 0
        
        if not is_valid:
            self._log_activity(
                f"⚠️  File structure validation failed for {phase_name}: "
                f"{len(missing_files)}/{len(expected_files)} files missing",
                level="WARNING"
            )
            for missing in missing_files[:5]:  # Log first 5
                self._log_activity(f"   - Missing: {missing}")
        else:
            self._log_activity(f"✅ File structure validation passed for {phase_name}: All {len(expected_files)} files created")
        
        return {
            'valid': is_valid,
            'missing_files': missing_files,
            'created_files': created_files,
            'expected_files': expected_files
        }
    
    def _validate_tech_stack_matches_vision(self, tech_stack_text: str, vision: str) -> bool:
        """
        Validate that the generated tech stack aligns with the vision.
        Returns True if valid, False if there's a mismatch.
        """
        vision_lower = vision.lower()
        stack_lower = tech_stack_text.lower()
        
        # Check for specific technology mentions in vision
        tech_keywords = {
            'react native': ['react native', 'react-native', 'reactnative'],
            'react': ['react', 'jsx', 'tsx'],
            'flutter': ['flutter', 'dart'],
            'python': ['python', 'py', 'flask', 'django', 'fastapi'],
            'node': ['node', 'express', 'nodejs'],
            'mobile': ['mobile', 'ios', 'android', 'app'],
        }
        
        for tech, keywords in tech_keywords.items():
            # If vision mentions this tech
            if any(kw in vision_lower for kw in keywords):
                # Check if stack includes it
                if not any(kw in stack_lower for kw in keywords):
                    self._log_activity(
                        f"⚠️  Tech stack mismatch: vision mentions '{tech}' but stack doesn't include it",
                        level="WARNING"
                    )
                    return False
        
        return True
    
    def _classify_stack_requirements(self, tech_stack_text: str) -> Dict[str, Any]:
        """
        Decide whether backend and frontend are required using an LLM classification of the tech stack.
        Returns {"backend_required": bool, "frontend_required": bool}.
        """
        if not tech_stack_text:
            return {"backend_required": True, "frontend_required": True}
        
        backend_flag = True
        frontend_flag = True
        
        try:
            import litellm
            
            model = os.getenv("LLM_MODEL_MANAGER", "gpt-4o-mini")
            if os.getenv("OPENROUTER_API_KEY"):
                clean_model = model.replace(":free", "").strip()
                if not clean_model.startswith("openrouter/"):
                    model = f"openrouter/{clean_model}"
            
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a classifier. Given a technology stack description, respond with JSON:\n"
                        '{\"backend_required\": true|false, \"frontend_required\": true|false, \"reason\": \"...\"}.\n'
                        "If the stack is purely CLI/console with no UI, set frontend_required=false.\n"
                        "If the stack is purely UI/front-end with no server/API, set backend_required=false.\n"
                        "If uncertain, err on the side of true for the relevant flag."
                    )
                },
                {"role": "user", "content": tech_stack_text[:6000]}
            ]
            
            resp = litellm.completion(model=model, messages=messages, temperature=0)
            content = resp["choices"][0]["message"]["content"]
            
            import json
            try:
                parsed = json.loads(content)
                backend_flag = bool(parsed.get("backend_required", True))
                frontend_flag = bool(parsed.get("frontend_required", True))
                self._log_activity(
                    f"LLM stack decision: backend={backend_flag}, frontend={frontend_flag} "
                    f"(reason: {parsed.get('reason', 'n/a')})"
                )
            except Exception:
                text = content.lower()
                if "backend_required" in text and "false" in text:
                    backend_flag = False
                if "frontend_required" in text and "false" in text:
                    frontend_flag = False
                self._log_activity(f"LLM stack decision (fallback parse): backend={backend_flag}, frontend={frontend_flag} | raw='{content[:200]}'")
            
        except Exception as e:
            self._log_activity(f"⚠️  LLM stack decision failed, falling back to heuristic: {e}", level="WARNING")
            text = tech_stack_text.lower()
            frontend_signals = ["frontend", "web", "html", "css", "react", "vue", "angular", "ui"]
            backend_signals = ["api", "server", "backend", "python", "node", "fastapi", "django", "flask"]
            cli_signals = ["cli", "console", "terminal"]
            has_frontend = any(sig in text for sig in frontend_signals)
            has_backend = any(sig in text for sig in backend_signals)
            has_cli_only = any(sig in text for sig in cli_signals) and not has_frontend
            frontend_flag = has_frontend or not has_cli_only
            backend_flag = has_backend or not has_frontend  # if no frontend mentioned, assume backend unless CLI-only

        # Guard: coding agent should generate code; avoid both flags false
        if not backend_flag and not frontend_flag:
            backend_flag = True
            self._log_activity("LLM stack decision adjusted: forcing backend_required=True to ensure code generation")
        
        return {"backend_required": backend_flag, "frontend_required": frontend_flag}
        
    def _log_activity(self, message: str, level: str = "INFO"):
        """Log activity to file and update progress if callback available"""
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] [{level}] {message}\n"
        
        try:
            with open(self.activity_log_file, 'a') as f:
                f.write(log_entry)
        except Exception as e:
            logger.warning(f"Could not write to activity log: {e}")
        
        # Also log to standard logger
        if level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
        else:
            logger.info(message)
    
    def _update_progress(self, phase: str, progress: int, message: str = None):
        """Update job progress via callback"""
        if self.progress_callback:
            try:
                self.progress_callback(phase, progress, message)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")
        
        if message:
            self._log_activity(f"[{phase}] {message}")
        else:
            self._log_activity(f"Phase: {phase} | Progress: {progress}%")
    
    def run(self, user_vision: str, design_specs_path: str = None, design_specs_urls: List[str] = None) -> Dict[str, Any]:
        """
        Execute the full development workflow with retry and state persistence
        """
        # Load design specifications from files and URLs
        design_specs = load_design_specs(design_specs_path, design_specs_urls)
        specs_text = format_specs_for_prompt(design_specs) if design_specs else ""
        
        # Show LLM configuration
        print_llm_config()
        
        # Show design specs summary if available
        if design_specs:
            console.print(f"\n[bold cyan]📋 Design Specifications Loaded[/bold cyan]")
            console.print(get_specs_summary(design_specs))
        
        console.print(Panel.fit(
            f"[bold cyan]AI Software Development Crew[/bold cyan]\n"
            f"[yellow]Vision:[/yellow] {user_vision}",
            border_style="cyan"
        ))
        
        # Check for saved state
        saved_state = self.state_manager.load_state()
        if saved_state:
            console.print(f"\n[bold yellow]📂 Resuming from saved state (phase: {saved_state.get('phase')})[/bold yellow]")
        
        results = {}
        agent_backstories = {}
        
        try:
            # Phase 0: Meta-Crew (Dynamic Agent Configuration)
            if not saved_state or saved_state.get('phase') == 'meta':
                self._update_progress('meta', 5, "Starting Meta-Crew phase (generating agent configurations)...")
                console.print("\n[bold cyan]Phase 0: Meta-Crew (Agent Configuration)[/bold cyan]")
                
                # Transition to META state
                try:
                    self.state_machine.transition(ProjectState.META, TransitionContext('meta', {}))
                except ValueError:
                    pass  # Already in META state
                
                while self.failure_counts['meta'] < settings.MAX_RETRY_ATTEMPTS:
                    try:
                        meta_result = self._run_meta_phase(user_vision)
                        agent_backstories = meta_result.get('agent_backstories', {})
                        context_digest = meta_result.get('context_digest', '')
                        
                        results['meta_crew'] = {
                            'agent_backstories': agent_backstories,
                            'context_digest': context_digest
                        }
                        self._update_progress('meta', 10, "Meta-Crew completed")
                        self.state_manager.save_state('product_owner', {'meta_crew': results['meta_crew']})
                        
                        # Transition to PRODUCT_OWNER state
                        self.state_machine.transition(ProjectState.PRODUCT_OWNER, TransitionContext('product_owner', results['meta_crew']))
                        # Clear failure counter on success
                        self.failure_counts.pop('meta', None)
                        break
                    except Exception as e:
                        analysis = self._handle_phase_error('meta', e, ProjectState.META)
                        if analysis.get('suggests_retry') and self.failure_counts['meta'] < settings.MAX_RETRY_ATTEMPTS:
                            continue
                        raise
            else:
                self._log_activity("⏭️  Skipping Phase 0 (already completed)")
                meta_crew_data = saved_state['data'].get('meta_crew', {})
                agent_backstories = meta_crew_data.get('agent_backstories', {})
                context_digest = meta_crew_data.get('context_digest', '')
                results['meta_crew'] = meta_crew_data
            
            # Phase 1: Product Owner - Create User Stories
            meta_crew_data = results.get('meta_crew', {})
            # Ensure context_digest is defined if we skipped phase 0
            if 'context_digest' not in locals():
                context_digest = meta_crew_data.get('context_digest', '')
                
            if not saved_state or saved_state.get('phase') in ['meta', 'product_owner']:
                self._update_progress('product_owner', 15, "Starting Product Owner phase")
                console.print("\n[bold green]Phase 1: Product Owner (User Stories)[/bold green]")
                
                while self.failure_counts['product_owner'] < settings.MAX_RETRY_ATTEMPTS:
                    try:
                        user_stories = self._run_product_owner_phase(user_vision, context_digest, agent_backstories)
                        results['user_stories'] = user_stories
                        
                        self._update_progress('product_owner', 25, "User stories created")
                        self.state_manager.save_state('designer', {'meta_crew': agent_backstories, 'user_stories': user_stories})
                        # Persist PO output if files missing, then validate
                        self._persist_artifact_if_missing('requirements.md', user_stories)
                        self._persist_artifact_if_missing('user_stories.md', user_stories)
                        self._require_artifact('requirements.md')
                        
                        # user_stories.md is optional if feature files exist
                        feature_dir = settings.WORKSPACE_PATH / "features"
                        has_feature_files = feature_dir.exists() and any(feature_dir.glob("*.feature"))
                        user_stories_file = settings.WORKSPACE_PATH / "user_stories.md"
                        
                        if not user_stories_file.exists() and not has_feature_files:
                            raise FileNotFoundError("Required artifact missing: user_stories.md (and no feature files found)")
                        elif not user_stories_file.exists():
                            self._log_activity("ℹ️ user_stories.md missing but feature files exist - proceeding")
                        
                        # Transition to DESIGNER state
                        try:
                            self.state_machine.transition(ProjectState.DESIGNER, TransitionContext('designer', {'user_stories': user_stories}))
                        except ValueError:
                            pass  # Already in correct state
                        self.failure_counts.pop('product_owner', None)
                        break
                    except Exception as e:
                        analysis = self._handle_phase_error('product_owner', e, ProjectState.PRODUCT_OWNER)
                        if analysis.get('suggests_retry') and self.failure_counts['product_owner'] < settings.MAX_RETRY_ATTEMPTS:
                            continue
                        raise
            else:
                self._log_activity("⏭️  Skipping Phase 1 (already completed)")
                results['user_stories'] = saved_state['data'].get('user_stories', '')
            
            # Phase 2: Designer - Create Design Spec
            if not saved_state or saved_state.get('phase') in ['meta', 'product_owner', 'designer']:
                self._update_progress('designer', 30, "Starting Designer phase")
                console.print("\n[bold green]Phase 2: Designer (Architecture Design)[/bold green]")
                
                while self.failure_counts['designer'] < settings.MAX_RETRY_ATTEMPTS:
                    try:
                        design_spec = self._run_designer_phase(results.get('user_stories', ''), context_digest, agent_backstories)
                        results['design_spec'] = design_spec
                        
                        self._update_progress('designer', 40, "Design specification created")
                        self.state_manager.save_state('tech_architect', {
                            'meta_crew': agent_backstories, 
                            'user_stories': results.get('user_stories', ''), 
                            'design_spec': design_spec
                        })

                        # Persist and require design_spec.md to avoid fallback-only content
                        self._persist_artifact_if_missing('design_spec.md', design_spec)
                        self._require_artifact('design_spec.md')
                        
                        # Transition to TECH_ARCHITECT state
                        try:
                            self.state_machine.transition(ProjectState.TECH_ARCHITECT, TransitionContext('tech_architect', {'design_spec': design_spec}))
                        except ValueError:
                            pass  # Already in correct state
                        self.failure_counts.pop('designer', None)
                        break
                    except Exception as e:
                        analysis = self._handle_phase_error('designer', e, ProjectState.DESIGNER)
                        if analysis.get('suggests_retry') and self.failure_counts['designer'] < settings.MAX_RETRY_ATTEMPTS:
                            continue
                        raise
            else:
                self._log_activity("⏭️  Skipping Phase 2 (already completed)")
                results['design_spec'] = saved_state['data'].get('design_spec', '')
            
            # Phase 3: Tech Architect - Define Tech Stack
            if not saved_state or saved_state.get('phase') in ['meta', 'product_owner', 'designer', 'tech_architect']:
                self._update_progress('tech_architect', 45, "Starting Tech Architect phase")
                console.print("\n[bold green]Phase 3: Tech Architect (Technology Stack)[/bold green]")
                
                while self.failure_counts['tech_architect'] < settings.MAX_RETRY_ATTEMPTS:
                    try:
                        tech_stack = self._run_tech_architect_phase(results.get('design_spec', ''), context_digest, user_vision, agent_backstories)
                        results['tech_stack'] = tech_stack
                        
                        # Validate tech stack matches vision
                        if not self._validate_tech_stack_matches_vision(tech_stack, user_vision):
                            raise ValueError(f"Tech stack does not match vision requirements. Vision: '{user_vision[:100]}...'")
                        
                        stack_requirements = self._classify_stack_requirements(tech_stack)
                        
                        self._update_progress('tech_architect', 55, "Technology stack defined")
                        self.state_manager.save_state('development', {
                            'meta_crew': agent_backstories, 
                            'user_stories': results.get('user_stories', ''), 
                            'design_spec': results.get('design_spec', ''), 
                            'tech_stack': tech_stack
                        })
                        
                        # Transition to DEVELOPMENT state
                        try:
                            self.state_machine.transition(ProjectState.DEVELOPMENT, TransitionContext('development', {'tech_stack': tech_stack}))
                        except ValueError:
                            pass  # Already in correct state
                        self.failure_counts.pop('tech_architect', None)
                        break
                    except Exception as e:
                        analysis = self._handle_phase_error('tech_architect', e, ProjectState.TECH_ARCHITECT)
                        if analysis.get('suggests_retry') and self.failure_counts['tech_architect'] < settings.MAX_RETRY_ATTEMPTS:
                            continue
                        raise
            else:
                self._log_activity("⏭️  Skipping Phase 3 (already completed)")
                results['tech_stack'] = saved_state['data'].get('tech_stack', '')
            
            # Phase 4: Developer - Implement using Tech Stack
            if not saved_state or saved_state.get('phase') in ['meta', 'product_owner', 'designer', 'tech_architect', 'development']:
                backend_required = stack_requirements.get('backend_required', True)
                frontend_required = stack_requirements.get('frontend_required', True)
                
                # Ensure src structure exists for dev outputs
                (settings.WORKSPACE_PATH / "src").mkdir(parents=True, exist_ok=True)
                (settings.WORKSPACE_PATH / "src" / "tests").mkdir(parents=True, exist_ok=True)
                
                if not backend_required:
                    self._log_activity("⏭️  Skipping Development phase (backend not required)")
                    results['implementation'] = None
                    # Transition towards frontend or completed based on frontend flag
                    if frontend_required:
                        try:
                            self.state_machine.transition(ProjectState.FRONTEND, TransitionContext('frontend', {'implementation': None, 'backend_required': False}))
                        except ValueError:
                            pass
                    else:
                        try:
                            self.state_machine.transition(ProjectState.COMPLETED, TransitionContext('completed', {'backend_required': False, 'frontend_required': False}))
                        except ValueError:
                            self.state_machine.force_transition(ProjectState.COMPLETED, TransitionContext('completed', {'backend_required': False, 'frontend_required': False}))
                else:
                    self._update_progress('development', 60, "Starting Development phase")
                    console.print("\n[bold green]Phase 4: Developer (Implementation)[/bold green]")
                    
                    while self.failure_counts['development'] < settings.MAX_RETRY_ATTEMPTS:
                        try:
                            implementation = self._run_dev_phase(results.get('user_stories', ''), results.get('design_spec', ''), specs_text, agent_backstories)
                            results['implementation'] = implementation
                            
                            self._update_progress('development', 90, "Implementation completed")
                            self.state_manager.save_state('frontend', results)
                            
                            # Validate code artifacts exist
                            workspace = settings.WORKSPACE_PATH
                            src_dir = workspace / "src"
                            if not src_dir.exists():
                                raise FileNotFoundError("Expected src directory to be created during development phase")
                            
                            impl_files = [
                                p for p in src_dir.rglob("*")
                                if p.is_file()
                                and (
                                    p.suffix in {".py", ".js", ".ts", ".tsx", ".jsx"}
                                )
                                and "test" not in p.name.lower()
                            ]
                            if not impl_files:
                                raise FileNotFoundError("No implementation code artifacts found under src/")
                            
                            test_files = [
                                p for p in src_dir.rglob("*")
                                if p.is_file()
                                and p.suffix in {".py", ".js", ".ts", ".tsx", ".jsx"}
                                and ("test_" in p.name.lower() or "_test" in p.name.lower())
                            ]
                            if not test_files:
                                raise FileNotFoundError("No test files found under src/ (expected at least one test file)")
                            
                            # Post-processing validation: check against tech_stack.md
                            validation = self._validate_generated_files('development')
                            if not validation['valid'] and len(validation['missing_files']) > 0:
                                missing_list = ', '.join(validation['missing_files'][:5])
                                self._log_activity(
                                    f"⚠️  Incomplete file generation: {len(validation['missing_files'])} files missing from tech_stack.md: {missing_list}",
                                    level="WARNING"
                                )
                                
                                # Create a detailed message for retry
                                missing_msg = f"\n\nMISSING FILES (you must create these):\n"
                                for mf in validation['missing_files'][:10]:
                                    missing_msg += f"  - {mf}\n"
                                
                                raise FileNotFoundError(
                                    f"Incomplete file structure: {len(validation['missing_files'])} files from tech_stack.md not created. "
                                    f"You created {len(validation['created_files'])} files but tech_stack.md specifies {len(validation['expected_files'])} files."
                                    f"{missing_msg}"
                                )
                            
                            if frontend_required:
                                try:
                                    self.state_machine.transition(ProjectState.FRONTEND, TransitionContext('frontend', {'implementation': implementation}))
                                except ValueError:
                                    pass  # Already in correct state
                            else:
                                try:
                                    self.state_machine.transition(ProjectState.COMPLETED, TransitionContext('completed', results))
                                except ValueError:
                                    self.state_machine.force_transition(ProjectState.COMPLETED, TransitionContext('completed', results))
                            self.failure_counts.pop('development', None)
                            break
                        except Exception as e:
                            analysis = self._handle_phase_error('development', e, ProjectState.DEVELOPMENT)
                            if analysis.get('suggests_retry') and self.failure_counts['development'] < settings.MAX_RETRY_ATTEMPTS:
                                continue
                            raise
            else:
                self._log_activity("⏭️  Skipping Phase 4 (already completed)")
                results['implementation'] = saved_state['data'].get('implementation', '')
            
            # Phase 5: Frontend Development (if needed)
            if not saved_state or saved_state.get('phase') in ['meta', 'product_owner', 'designer', 'tech_architect', 'development', 'frontend']:
                frontend_required = stack_requirements.get('frontend_required', True)
                
                if not frontend_required:
                    self._log_activity("⏭️  Skipping Frontend phase (frontend not required)")
                    try:
                        self.state_machine.transition(ProjectState.COMPLETED, TransitionContext('completed', results))
                    except ValueError:
                        self.state_machine.force_transition(ProjectState.COMPLETED, TransitionContext('completed', results))
                    self.failure_counts.pop('frontend', None)
                else:
                    self._update_progress('frontend', 92, "Starting Frontend Development phase")
                    console.print("\n[bold green]Phase 5: Frontend Developer (UI/UX)[/bold green]")
                    
                    while self.failure_counts['frontend'] < settings.MAX_RETRY_ATTEMPTS:
                        try:
                            frontend_implementation = self._run_frontend_phase(results.get('user_stories', ''), results.get('design_spec', ''), specs_text, agent_backstories)
                            results['frontend_implementation'] = frontend_implementation
                            
                            self._update_progress('frontend', 98, "Frontend Development completed")
                            
                            # If UI files are missing, log warning (stub creation removed)
                            self._ensure_frontend_stub()
                            
                            # Validate UI artifacts exist (must have at least one UI file under src/)
                            workspace = settings.WORKSPACE_PATH
                            src_dir = workspace / "src"
                            has_ui_artifacts = any(
                                src_dir.rglob("*.html")
                            ) or any(
                                src_dir.rglob("*.js")
                            ) or any(
                                src_dir.rglob("*.tsx")
                            ) or any(
                                src_dir.rglob("*.jsx")
                            ) or any(
                                src_dir.rglob("*.ts")
                            )
                            if not has_ui_artifacts:
                                raise FileNotFoundError("No UI artifacts found under src/ (expected at least one *.js/ts/tsx/jsx/html)")
                            
                            # Post-processing validation: check against tech_stack.md
                            validation = self._validate_generated_files('frontend')
                            if not validation['valid'] and len(validation['missing_files']) > 0:
                                missing_list = ', '.join(validation['missing_files'][:5])
                                self._log_activity(
                                    f"⚠️  Incomplete file generation: {len(validation['missing_files'])} files missing from tech_stack.md: {missing_list}",
                                    level="WARNING"
                                )
                                
                                # Create a detailed message for retry
                                missing_msg = f"\n\nMISSING FILES (you must create these):\n"
                                for mf in validation['missing_files'][:10]:
                                    missing_msg += f"  - {mf}\n"
                                
                                raise FileNotFoundError(
                                    f"Incomplete file structure: {len(validation['missing_files'])} files from tech_stack.md not created. "
                                    f"You created {len(validation['created_files'])} files but tech_stack.md specifies {len(validation['expected_files'])} files."
                                    f"{missing_msg}"
                                )
                            
                            # Transition to COMPLETED state
                            try:
                                self.state_machine.transition(ProjectState.COMPLETED, TransitionContext('completed', results))
                            except ValueError:
                                pass  # Already in correct state
                            self.failure_counts.pop('frontend', None)
                            break
                        except Exception as e:
                            analysis = self._handle_phase_error('frontend', e, ProjectState.FRONTEND)
                            if analysis.get('suggests_retry') and self.failure_counts['frontend'] < settings.MAX_RETRY_ATTEMPTS:
                                continue
                            raise
            else:
                self._log_activity("⏭️  Skipping Phase 5 (already completed)")
                results['frontend_implementation'] = saved_state['data'].get('frontend_implementation', '')
            
            # Clear state on success
            self.state_manager.clear_state()
            
            # Show budget report
            self._show_budget_report()
            
            self._update_progress('completed', 100, "All phases completed successfully")
            self._log_activity("✅ Development Complete! All phases finished successfully")
            console.print("\n[bold green]✅ Development Complete![/bold green]")
            
            return results
            
        except Exception as e:
            error_msg = f"Error in orchestration: {str(e)}"
            logger.error(error_msg, exc_info=True)
            console.print(f"\n[bold red]❌ Error: {str(e)}[/bold red]")
            console.print(f"\n[bold yellow]💾 State saved. Resume by running the same command again.[/bold yellow]")
            raise

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _show_budget_report(self):
        """Display budget report"""
        report = self.budget_tracker.get_report(self.project_id)
        console.print(Panel(
            f"Total Cost: ${report['total_cost']:.4f}\n"
            f"Budget Used: {report['budget_used_pct']:.1f}%\n"
            f"Remaining: ${report['budget_remaining']:.4f}",
            title="Budget Report",
            border_style="green"
        ))

    def _run_meta_phase(self, vision: str) -> Dict[str, Any]:
        """Run Meta-Crew to generate dynamic agent backstories and context digest"""
        console.print("  🎭 Generating dynamic agent configurations...")
        
        budget_status = self.budget_tracker.check_budget(self.project_id)
        if not budget_status['allowed']:
            raise Exception(f"Budget exceeded: {budget_status['message']}")
        
        meta_crew = MetaCrew()
        crew_instance = meta_crew.crew()
        
        inputs = {"vision": vision}
        
        self._update_progress('meta', 7, "Running Meta-Crew (generating agent personas)...")
        result = safe_execute_with_retry(
            lambda: crew_instance.kickoff(inputs=inputs),
            max_attempts=settings.MAX_RETRY_ATTEMPTS,
            default_wait=60.0,
            save_state=True
        )
        
        result_text = str(result)
        context_digest = ""
        agent_backstories = {}
        
        import re
        import json
        
        context_match = re.search(r'Project Context Digest[:\s]*(.*?)(?=\n\n|\n\{|$)', result_text, re.DOTALL | re.IGNORECASE)
        if context_match:
            context_digest = context_match.group(1).strip()
            
        try:
            cleaned_output = result_text.replace('```json', '').replace('```', '').strip()
            json_pattern = r'\{[^{}]*(?:"product_owner"|"high_level_designer"|"tech_architect"|"coder"|"developer"|"tester")[^{}]*\}'
            json_match = re.search(json_pattern, cleaned_output, re.DOTALL)
            if json_match:
                cleaned_output = json_match.group(0)
            else:
                json_blocks = re.findall(r'\{[^{}]*\}', cleaned_output, re.DOTALL)
                if json_blocks:
                    cleaned_output = max(json_blocks, key=len)
            
            agent_backstories = json.loads(cleaned_output)
            
            prompts_file = settings.WORKSPACE_PATH / "agent_prompts.json"
            with open(prompts_file, 'w') as f:
                json.dump(agent_backstories, f, indent=2)
                
            self._log_activity("✅ Successfully parsed agent backstories from Meta-Crew")
            
        except (json.JSONDecodeError, KeyError) as e:
            self._log_activity(f"WARNING: Could not parse agent backstories, using defaults: {e}", level="WARNING")
            agent_backstories = {}
            
        if context_digest:
            context_file = settings.WORKSPACE_PATH / "context_digest.md"
            with open(context_file, 'w') as f:
                f.write("# Project Context Digest\n\n")
                f.write(context_digest)
                
        return {
            'agent_backstories': agent_backstories,
            'context_digest': context_digest
        }

    def _run_product_owner_phase(self, vision: str, context_digest: str, agent_backstories: Dict[str, str] = None) -> str:
        console.print("  📋 Creating user stories from vision...")
        
        budget_status = self.budget_tracker.check_budget(self.project_id)
        if not budget_status['allowed']:
            raise Exception(f"Budget exceeded: {budget_status['message']}")
            
        po_crew = ProductOwnerCrew()
        if agent_backstories and agent_backstories.get('product_owner'):
            po_crew._custom_backstories = {'product_owner': agent_backstories['product_owner']}
            
        crew_instance = po_crew.crew()
        inputs = {
            "vision": vision,
            "context_digest": context_digest if context_digest else "No context digest available."
        }
        
        self._update_progress('product_owner', 20, "Running Product Owner crew...")
        result = safe_execute_with_retry(
            lambda: crew_instance.kickoff(inputs=inputs),
            max_attempts=settings.MAX_RETRY_ATTEMPTS,
            default_wait=60.0,
            save_state=True
        )
        
        # Read actual file content (agents should have written it)
        user_stories_file = settings.WORKSPACE_PATH / "user_stories.md"
        if user_stories_file.exists():
            try:
                with open(user_stories_file, 'r') as f:
                    user_stories_content = f.read()
                self._log_activity("✅ Read user_stories.md from disk")
                console.print(Panel(user_stories_content[:500] + "..." if len(user_stories_content) > 500 else user_stories_content, title="User Stories", border_style="green"))
                return user_stories_content
            except Exception as e:
                self._log_activity(f"ERROR: Could not read user_stories.md: {e}", level="ERROR")
        
        # Fallback: use result string
        self._log_activity("⚠️  user_stories.md not found, using crew output as fallback", level="WARNING")
        console.print(Panel(str(result), title="User Stories (Fallback)", border_style="yellow"))
        return str(result)

    def _run_designer_phase(self, user_stories: str, context_digest: str, agent_backstories: Dict[str, str] = None) -> str:
        console.print("  🎨 Creating design specification...")
        
        budget_status = self.budget_tracker.check_budget(self.project_id)
        if not budget_status['allowed']:
            raise Exception(f"Budget exceeded: {budget_status['message']}")
            
        designer_crew = DesignerCrew()
        if agent_backstories and agent_backstories.get('high_level_designer'):
            designer_crew._custom_backstories = {'high_level_designer': agent_backstories['high_level_designer']}
            
        crew_instance = designer_crew.crew()
        inputs = {
            "user_stories": user_stories,
            "context_digest": context_digest if context_digest else "No context digest available."
        }
        
        self._update_progress('designer', 35, "Running Designer crew...")
        result = safe_execute_with_retry(
            lambda: crew_instance.kickoff(inputs=inputs),
            max_attempts=settings.MAX_RETRY_ATTEMPTS,
            default_wait=60.0,
            save_state=True
        )
        
        # Read actual file content (agents should have written it)
        design_spec_file = settings.WORKSPACE_PATH / "design_spec.md"
        if design_spec_file.exists():
            try:
                with open(design_spec_file, 'r') as f:
                    design_spec_content = f.read()
                self._log_activity("✅ Read design_spec.md from disk")
                console.print(Panel(design_spec_content[:500] + "..." if len(design_spec_content) > 500 else design_spec_content, title="Design Specification", border_style="green"))
                return design_spec_content
            except Exception as e:
                self._log_activity(f"ERROR: Could not read design_spec.md: {e}", level="ERROR")
        
        # Fallback: use result string
        self._log_activity("⚠️  design_spec.md not found, using crew output as fallback", level="WARNING")
        console.print(Panel(str(result), title="Design Specification (Fallback)", border_style="yellow"))
        return str(result)

    def _run_tech_architect_phase(self, design_spec: str, context_digest: str, vision: str, agent_backstories: Dict[str, str] = None) -> str:
        console.print("  🏗️  Defining technology stack...")
        
        budget_status = self.budget_tracker.check_budget(self.project_id)
        if not budget_status['allowed']:
            raise Exception(f"Budget exceeded: {budget_status['message']}")
            
        tech_architect_crew = TechArchitectCrew()
        if agent_backstories and agent_backstories.get('tech_architect'):
            tech_architect_crew._custom_backstories = {'tech_architect': agent_backstories['tech_architect']}
            
        crew_instance = tech_architect_crew.crew()
        inputs = {
            "design_spec": design_spec,
            "context_digest": context_digest if context_digest else "No context digest available.",
            "vision": vision
        }
        
        # Try up to 3 times to get tech_stack.md created
        max_file_attempts = 3
        tech_stack_file = settings.WORKSPACE_PATH / "tech_stack.md"
        
        for attempt in range(1, max_file_attempts + 1):
            self._update_progress('tech_architect', 50, f"Running Tech Architect crew (attempt {attempt}/{max_file_attempts})...")
            result = safe_execute_with_retry(
                lambda: crew_instance.kickoff(inputs=inputs),
                max_attempts=settings.MAX_RETRY_ATTEMPTS,
                default_wait=60.0,
                save_state=True
            )
            
            # Check if file was created
            if tech_stack_file.exists():
                try:
                    with open(tech_stack_file, 'r') as f:
                        tech_stack_content = f.read()
                    self._log_activity(f"✅ Read tech_stack.md from disk (attempt {attempt})")
                    console.print(Panel(tech_stack_content[:500] + "..." if len(tech_stack_content) > 500 else tech_stack_content, title="Technology Stack", border_style="green"))
                    return tech_stack_content
                except Exception as e:
                    self._log_activity(f"ERROR: Could not read tech_stack.md: {e}", level="ERROR")
            else:
                self._log_activity(f"⚠️  tech_stack.md not found after attempt {attempt}", level="WARNING")
                if attempt < max_file_attempts:
                    console.print(f"[yellow]⚠️  tech_stack.md not created. Retrying ({attempt}/{max_file_attempts})...[/yellow]")
                else:
                    console.print("[red]❌ tech_stack.md not created after 3 attempts. Using fallback.[/red]")
        
        # Fallback: use result string
        self._log_activity("⚠️  tech_stack.md not found after all attempts, using crew output as fallback", level="WARNING")
        console.print(Panel(str(result), title="Technology Stack (Fallback)", border_style="yellow"))
        return str(result)

    def _run_dev_phase(self, user_stories: str, design_spec: str, specs_text: str = "", agent_backstories: Dict[str, str] = None) -> str:
        console.print("  💻 Implementing features using TDD...")
        
        budget_status = self.budget_tracker.check_budget(self.project_id)
        if not budget_status['allowed']:
            raise Exception(f"Budget exceeded: {budget_status['message']}")
            
        dev_crew = DevCrew()
        if agent_backstories and agent_backstories.get('developer'):
            dev_crew._custom_backstories = {'developer': agent_backstories['developer']}
            
        crew_instance = dev_crew.crew()
        
        requirements_text = f"{user_stories}\n\n{design_spec}" if design_spec else user_stories
        inputs = {
            "requirements": requirements_text,
            "design_specs": specs_text if specs_text else "No design specifications provided."
        }
        
        self._update_progress('development', 55, "Running Development crew (implementing features with TDD)...")
        result = safe_execute_with_retry(
            lambda: crew_instance.kickoff(inputs=inputs),
            max_attempts=settings.MAX_RETRY_ATTEMPTS,
            default_wait=60.0,
            save_state=True
        )
        
        self._log_activity("Backend Development phase completed successfully")
        console.print(Panel(str(result), title="Backend Implementation", border_style="green"))
        return str(result)

    def _run_frontend_phase(self, requirements: str, design_specs: Dict[str, str] = None, specs_text: str = "", agent_backstories: Dict[str, str] = None) -> str:
        console.print("  🎨 Building user interface...")
        
        budget_status = self.budget_tracker.check_budget(self.project_id)
        if not budget_status['allowed']:
            raise Exception(f"Budget exceeded: {budget_status['message']}")
            
        frontend_crew = FrontendCrew()
        if agent_backstories and agent_backstories.get('frontend_developer'):
            frontend_crew._custom_backstories = {'frontend_developer': agent_backstories['frontend_developer']}
            
        crew_instance = frontend_crew.crew()
        
        inputs = {
            "requirements": requirements,
            "design_specs": specs_text if specs_text else "No design specifications provided."
        }
        
        result = safe_execute_with_retry(
            lambda: crew_instance.kickoff(inputs=inputs),
            max_attempts=settings.MAX_RETRY_ATTEMPTS,
            default_wait=60.0,
            save_state=True
        )
        
        console.print(Panel(str(result), title="Frontend Implementation", border_style="green"))
        return str(result)
    
    def _handle_phase_error(self, phase: str, error: Exception, current_state: ProjectState):
        """Handle errors during phase execution with recovery strategies"""
        self.failure_counts[phase] += 1
        failure_count = self.failure_counts[phase]
        
        error_context = ErrorContext(
            error_type=type(error).__name__,
            failed_agent=phase,
            error_message=str(error),
            failure_count=failure_count,
            rollback_target=self._determine_rollback_target(current_state),
            recovery_actions=[f"Retry {phase} phase", f"Check {phase} agent configuration"]
        )
        
        # Analyze error
        analysis = self.error_recovery.analyze_error(error_context)
        
        self._log_activity(
            f"❌ Error in {phase} phase (attempt {failure_count}): {error_context.error_message}",
            level="ERROR"
        )
        self._log_activity(f"Recovery strategy: {analysis.get('reason', 'unknown')}")
        
        # Act on suggested recovery steps
        if analysis.get('suggests_rollback'):
            target_state = analysis.get('rollback_target', self._determine_rollback_target(current_state))
            rolled_back = self.state_machine.rollback_to(target_state)
            if rolled_back:
                self._log_activity(f"🔄 Rolled back to {target_state.value} after failure in {phase}")
            else:
                self._log_activity(f"⚠️  Rollback to {target_state.value} failed", level="WARNING")
        
        if analysis.get('suggests_reassignment'):
            alternative = analysis.get('alternative_agent')
            if alternative:
                self._log_activity(f"👥 Suggest reassigning {phase} to {alternative}", level="WARNING")
            else:
                self._log_activity("👥 Suggest reassigning agent for phase due to repeated failures", level="WARNING")
        
        # Escalate to FAILED state when attempts are exhausted
        if failure_count >= settings.MAX_RETRY_ATTEMPTS and not analysis.get('suggests_retry'):
            self._mark_failed()
        
        return analysis
    
    def _mark_failed(self):
        """Mark the project as failed, forcing state if validation blocks"""
        try:
            self.state_machine.transition(ProjectState.FAILED)
            self._log_activity("Project marked as FAILED - requires human intervention", level="ERROR")
        except ValueError:
            # Fall back to forceful transition to ensure terminal marking
            self.state_machine.force_transition(ProjectState.FAILED, TransitionContext('failed', {}))
            self._log_activity("Project forcibly marked as FAILED", level="ERROR")
    
    def _determine_rollback_target(self, current_state: ProjectState) -> ProjectState:
        """Determine rollback target based on current state"""
        if current_state == ProjectState.PRODUCT_OWNER:
            return ProjectState.META
        elif current_state == ProjectState.DESIGNER:
            return ProjectState.PRODUCT_OWNER
        elif current_state == ProjectState.TECH_ARCHITECT:
            return ProjectState.DESIGNER
        elif current_state == ProjectState.DEVELOPMENT:
            return ProjectState.TECH_ARCHITECT
        elif current_state == ProjectState.FRONTEND:
            return ProjectState.DEVELOPMENT
        else:
            return ProjectState.META


def run_orchestrator(vision: str, design_specs_path: str = None, design_specs_urls: List[str] = None):
    """
    Convenience function to run the orchestrator
    """
    orchestrator = SoftwareDevOrchestrator()
    return orchestrator.run(vision, design_specs_path, design_specs_urls)

def review_codebase(path: str):
    """
    Convenience function to review codebase
    """
    from ..crews.code_review_crew import CodeReviewCrew
    
    console.print(Panel(f"Running Code Review on: {path}", title="Code Review", border_style="cyan"))
    
    crawler_crew = CodeReviewCrew()
    crew_instance = crawler_crew.crew()
    
    # Logic for review is slightly different, requires specific inputs
    # This is a placeholder for the actual review logic
    pass
