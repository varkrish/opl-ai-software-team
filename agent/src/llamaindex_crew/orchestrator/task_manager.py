"""
Task Manager with SQLite persistence
Manages task registry, creation tracking, and execution validation
"""
import os
import sqlite3
import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Dict, Optional, Any
from pathlib import Path
from datetime import datetime
import re
from ..tools.file_tools import _sanitize_java_package_path
from ..utils.prompt_budget import PromptBudget, estimate_tokens, trim_text
from ..utils.test_companion import (
    companion_source_exists_on_disk,
    is_test_file_path,
    resolve_companion_source,
)
from ..utils.test_task_paths import derive_tdd_test_paths
from ..utils.vision_stack_analysis import (
    component_reflected_in_artifact,
    extract_named_components,
)

logger = logging.getLogger(__name__)

# Blacklist for registration: reject dangerous binaries and image assets only.
# Any other extension (go.mod, Cargo.lock, .svg, …) is accepted.
REJECTED_FILE_EXTENSIONS = frozenset({
    # Dangerous / executable / native binaries
    'exe', 'dll', 'so', 'dylib', 'o', 'a', 'bin', 'com',
    'bat', 'cmd', 'msi', 'scr',
    'class', 'pyc', 'pyo', 'wasm',
    # Pictures / media images
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'bmp',
    'tif', 'tiff', 'heic', 'avif',
})

# Extensionless basenames that are still real project artifacts.
EXTENSIONLESS_FILENAMES = frozenset({
    'dockerfile', 'containerfile', 'makefile', 'gnumakefile',
    'gemfile', 'procfile', 'rakefile', 'brewfile',
    'license', 'licence', 'notice', 'authors', 'contributors',
    'copying', 'changelog', 'changes', 'history',
    'cargo.lock',  # defensive; normally matched via .lock extension
    'pipfile', 'vagrantfile', 'jenkinsfile',
})


def _trim_existing_files(
    files: Dict[str, str],
    context_window: Optional[int],
    max_tokens: Optional[int],
    static_overhead_chars: int = 0,
) -> Dict[str, str]:
    """
    Trim per-file content in *files* so the combined total fits within the
    available input-token budget.

    The budget is computed from *context_window* and *max_tokens* (if given);
    when both are None a generous 8 000-token file-block budget is used so
    callers that don't pass LLM info still benefit from a safety cap.

    Files are trimmed proportionally — larger files contribute more of their
    content and are trimmed first to give smaller files their full content.
    """
    if not files:
        return files

    if context_window and max_tokens:
        budget = PromptBudget.from_context(context_window, max_tokens)
        file_block_budget = budget.input_token_budget - int(static_overhead_chars / 4)
        file_block_budget = max(file_block_budget, 2_000)
    else:
        file_block_budget = 8_000  # conservative default (~32 000 chars)

    total_tokens = sum(estimate_tokens(v) for v in files.values())
    if total_tokens <= file_block_budget:
        return files

    logger.info(
        "_trim_existing_files: %d files, %d tokens > budget %d. Trimming.",
        len(files), total_tokens, file_block_budget,
    )

    per_file_budget = max(200, file_block_budget // len(files))
    result = {}
    for fp, content in files.items():
        if estimate_tokens(content) > per_file_budget:
            content = trim_text(content, per_file_budget)
        result[fp] = content
    return result


def _is_valid_file_path(name: str) -> bool:
    """Return True if *name* looks like a real file path, not numbered-list junk or a blacklisted type.

    Registration gate is a blacklist (dangerous binaries + images). Unknown
    extensions are accepted so stacks like go.mod / go.sum register as tasks.
    Extensionless names are accepted only when the basename is in
    EXTENSIONLESS_FILENAMES (Dockerfile, Makefile, …).
    """
    if not name or len(name) < 2:
        return False
    # Reject purely numeric prefixes like "1.", "2.", "23."
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    if stem.isdigit():
        return False
    basename = name.rsplit('/', 1)[-1]
    # Dotfiles / multi-dot names: treat last segment after '.' as extension
    # unless the name is a known extensionless artifact (e.g. CMakeLists.txt is listed).
    if '.' not in basename:
        return basename.lower() in EXTENSIONLESS_FILENAMES
    # Names like "CMakeLists.txt" may be listed extensionless-style; also allow via ext
    if basename.lower() in EXTENSIONLESS_FILENAMES:
        return True
    ext = basename.rsplit('.', 1)[1].lower()
    if not ext:
        return False
    if ext in REJECTED_FILE_EXTENSIONS:
        return False
    return True


class TaskStatus(Enum):
    """Task execution status"""
    REGISTERED = "registered"      # Task defined but not created
    CREATED = "created"             # Task created in workflow
    IN_PROGRESS = "in_progress"     # Task currently executing
    COMPLETED = "completed"         # Task finished successfully
    FAILED = "failed"               # Task failed
    SKIPPED = "skipped"             # Task skipped (optional)


@dataclass
class TaskDefinition:
    """Definition of a required task"""
    task_id: str
    phase: str                      # Which phase (meta, dev, frontend, etc.)
    task_type: str                  # Type: feature, test, file, etc.
    description: str
    required: bool = True           # Must be completed
    dependencies: List[str] = None  # Task IDs that must complete first
    source: str = None              # Where task came from (feature file, tech_stack, etc.)
    status: str = None              # Current status (registered, completed, etc.)
    metadata: Dict = None           # Additional task data
    
    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []
        if self.metadata is None:
            self.metadata = {}


class TaskManager:
    """Manages task registry, creation tracking, and execution validation with SQLite"""
    
    def __init__(self, db_path: Path, project_id: str):
        self.db_path = db_path
        self.project_id = project_id
        self._init_database()
    
    def _init_database(self):
        """Initialize SQLite database with schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                task_type TEXT NOT NULL,
                description TEXT,
                required BOOLEAN DEFAULT 1,
                source TEXT,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                error_message TEXT,
                metadata TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_dependencies (
                task_id TEXT NOT NULL,
                depends_on_task_id TEXT NOT NULL,
                PRIMARY KEY (task_id, depends_on_task_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_execution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_project_phase 
            ON tasks(project_id, phase)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status 
            ON tasks(status)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_log_task_id 
            ON task_execution_log(task_id)
        """)
        
        conn.commit()
        conn.close()
    
    def register_task(self, task: TaskDefinition) -> None:
        """Register a new task in the database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        metadata_json = json.dumps(task.metadata) if task.metadata else None
        
        cursor.execute("""
            INSERT OR REPLACE INTO tasks 
            (task_id, project_id, phase, task_type, description, required, source, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.task_id, self.project_id, task.phase, task.task_type,
            task.description, task.required, task.source, TaskStatus.REGISTERED.value,
            metadata_json
        ))
        
        # Insert dependencies
        if task.dependencies:
            for dep_id in task.dependencies:
                cursor.execute("""
                    INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id)
                    VALUES (?, ?)
                """, (task.task_id, dep_id))
        
        conn.commit()
        conn.close()
        
        self._log_event(task.task_id, "registered", {"task": asdict(task)})
    
    def register_tasks_from_features(self, features: List[Dict]) -> List[TaskDefinition]:
        """Register tasks from parsed feature files"""
        tasks = []
        for feature in features:
            task = TaskDefinition(
                task_id=f"feature_{feature['name']}",
                phase="development",
                task_type="feature",
                description=feature.get('description', feature['name']),
                source=feature.get('file', 'features/'),
                metadata={"scenarios": feature.get('scenarios', [])}
            )
            self.register_task(task)
            tasks.append(task)
        return tasks
    
    def register_tasks_from_tech_stack(self, tech_stack_file: Path) -> List[TaskDefinition]:
        """Register required file creation tasks from tech_stack.md"""
        expected_files = self._extract_files_from_tech_stack(tech_stack_file)
        tasks = []
        
        for file_path in expected_files:
            task = TaskDefinition(
                task_id=f"file_{file_path.replace('/', '_').replace('.', '_')}",
                phase="development",
                task_type="file_creation",
                description=f"Create file: {file_path}",
                source=str(tech_stack_file),
                metadata={"file_path": file_path}
            )
            self.register_task(task)
            tasks.append(task)
        return tasks
    
    def _extract_files_from_tech_stack(self, tech_stack_file: Path) -> List[str]:
        """Extract file paths from tech_stack.md"""
        if not tech_stack_file.exists():
            return []
        
        try:
            with open(tech_stack_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find the "File Structure" section
            file_structure_match = re.search(
                r'## File Structure.*?```(?:[a-zA-Z]*)?\n([\s\S]*?)```', 
                content, 
                re.IGNORECASE | re.DOTALL
            )
            
            if not file_structure_match:
                # Fallback: find any code block that looks like a file tree
                code_blocks = re.findall(r'```(?:[a-zA-Z]*)?\n([\s\S]*?)```', content)
                for block in code_blocks:
                    if re.search(r'[├└│─]|^[a-zA-Z0-9_\-]+/', block, re.MULTILINE):
                        file_structure_block = block
                        break
                else:
                    return []
            else:
                file_structure_block = file_structure_match.group(1)
            
            # Skip JSON blocks
            if file_structure_block.strip().startswith('{') or '"dependencies"' in file_structure_block:
                return []
            
            expected_files = []
            lines = file_structure_block.split('\n')
            
            for line in lines:
                # Skip lines that look like JSON
                if '"' in line or '{' in line or '}' in line or line.strip().startswith('"'):
                    continue
                
                # Skip npm package names
                if line.strip().startswith('@') or '/node_modules/' in line:
                    continue
                
                # Extract file paths
                file_match = re.search(
                    r'[├└│─\s]*([a-zA-Z0-9_/\.\-]+(?:\.(?:js|jsx|ts|tsx|py|java|go|rs|cpp|h|c|json|md|config|lock|toml|yml|yaml|gitignore|txt|xml|java|gradle|podfile|xcworkspace))?)', 
                    line
                )
                if file_match:
                    file_path = file_match.group(1).strip()
                    
                    # Skip directory entries
                    if file_path.endswith('/'):
                        continue
                    
                    # Skip npm package names
                    if file_path.startswith(('@', 'babel/', 'react/', 'types/', 'eslint/', 'jest/', 'prettier/')):
                        continue
                    
                    # Skip if it looks like a package name without a file extension
                    if '/' in file_path and not re.search(
                        r'\.(js|jsx|ts|tsx|json|md|config|lock|toml|yml|yaml|gitignore|txt|xml|java|gradle|podfile|xcworkspace)$', 
                        file_path
                    ):
                        continue
                    
                    # Normalize path
                    root_level_files = [
                        'package.json', 'package-lock.json', 'yarn.lock',
                        'index.js', 'index.ts', 'index.tsx',
                        '.gitignore', '.eslintrc.js', '.prettierrc',
                        'babel.config.js', 'metro.config.js', 'app.json',
                        'jest.config.js', 'tsconfig.json', 'eslint.config.js',
                        'README.md', 'Dockerfile', 'docker-compose.yml',
                        'pom.xml', 'build.gradle', 'settings.gradle', 'mvnw', 'gradlew',
                        'pyproject.toml'
                    ]
                    
                    if '/' in file_path:
                        expected_files.append(_sanitize_java_package_path(file_path))
                    elif any(file_path.endswith(root) or file_path == root for root in root_level_files):
                        expected_files.append(file_path)
                    elif file_path.startswith(('android', 'ios', 'node_modules')):
                        continue
                    else:
                        expected_files.append(f'src/{file_path}')
            
            return list(set(expected_files))
        except Exception as e:
            logger.warning(f"Could not parse tech_stack.md for file structure: {e}")
            return []
    
    def mark_task_created(self, task_id: str) -> None:
        """Mark a task as created in the workflow"""
        self._update_task_status(task_id, TaskStatus.CREATED)
        self._log_event(task_id, "created", {})
    
    def mark_task_started(self, task_id: str) -> None:
        """Mark a task as started"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks 
            SET status = ?, started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
        """, (TaskStatus.IN_PROGRESS.value, task_id))
        conn.commit()
        conn.close()
        self._log_event(task_id, "started", {})
    
    def mark_task_executed(self, task_id: str, status: TaskStatus, error_message: str = None) -> None:
        """Mark a task as executed with status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if status == TaskStatus.COMPLETED:
            cursor.execute("""
                UPDATE tasks 
                SET status = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
            """, (status.value, task_id))
        else:
            cursor.execute("""
                UPDATE tasks 
                SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
            """, (status.value, error_message, task_id))
        
        conn.commit()
        conn.close()
        self._log_event(task_id, status.value, {"error": error_message} if error_message else {})
    
    def validate_all_tasks_created(self) -> Dict[str, Any]:
        """Validate that all required tasks were created"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT task_id FROM tasks 
            WHERE project_id = ? AND required = 1 AND status = ?
        """, (self.project_id, TaskStatus.REGISTERED.value))
        
        missing_tasks = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return {
            'valid': len(missing_tasks) == 0,
            'missing_tasks': missing_tasks
        }
    
    def validate_all_tasks_completed(self, workspace_path: Path = None) -> Dict[str, Any]:
        """Validate that all required tasks were completed, with optional physical verification"""
        if workspace_path:
            self.reconcile_corrupt_completed_files(workspace_path)
            self.reconcile_with_filesystem(workspace_path)
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT task_id, status FROM tasks 
            WHERE project_id = ? AND required = 1 
            AND status NOT IN (?, ?)
        """, (self.project_id, TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value))
        
        incomplete = []
        failed = []
        for row in cursor.fetchall():
            if row[1] == TaskStatus.FAILED.value:
                failed.append(row[0])
            else:
                incomplete.append(row[0])
        
        conn.close()
        
        return {
            'valid': len(incomplete) == 0 and len(failed) == 0,
            'incomplete_tasks': incomplete,
            'failed_tasks': failed
        }

    @staticmethod
    def resolve_planned_file_on_disk(workspace_path: Path, planned_path: str) -> Optional[Path]:
        """Resolve a planned relative file path on disk without ambiguous basename matches.

        Resolution order:
        1. Exact relative path under the workspace
        2. Unique suffix match (e.g. ``src/main.py`` → ``backend/src/main.py``)
        3. Basename match only when the planned path has no directory component and
           exactly one file in the workspace shares that basename
        """
        if not planned_path or not workspace_path.exists():
            return None

        planned = Path(planned_path.strip().lstrip("/"))
        exact = workspace_path / planned
        if exact.is_file():
            return exact

        planned_posix = planned.as_posix()
        suffix_matches: List[Path] = []
        for candidate in workspace_path.rglob("*"):
            if not candidate.is_file():
                continue
            rel = candidate.relative_to(workspace_path).as_posix()
            if rel == planned_posix or rel.endswith("/" + planned_posix):
                suffix_matches.append(candidate)

        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) > 1:
            return min(
                suffix_matches,
                key=lambda p: len(p.relative_to(workspace_path).parts),
            )

        # Basename-only fallback is unsafe for nested paths (e.g. multiple handler.go).
        if len(planned.parts) > 1:
            return None

        basename = planned.name
        basename_matches = [
            p for p in workspace_path.rglob("*")
            if p.is_file() and p.name == basename
        ]
        if len(basename_matches) == 1:
            return basename_matches[0]
        return None

    def reconcile_with_filesystem(self, workspace_path: Path):
        """Cross-check incomplete file creation tasks with the actual filesystem"""
        if not workspace_path.exists():
            return

        incomplete_tasks = self.get_incomplete_tasks()
        if not incomplete_tasks:
            return

        for task in incomplete_tasks:
            if task.task_type != "file_creation":
                continue
            file_path = (task.metadata or {}).get("file_path", "")
            if not file_path:
                continue

            found = self.resolve_planned_file_on_disk(workspace_path, file_path)
            if found is None:
                continue

            if not self._planned_file_is_complete(found):
                continue

            rel = found.relative_to(workspace_path)
            logger.info(
                "Self-healing: Found file %s for task %s at %s",
                file_path, task.task_id, rel,
            )
            self.update_task_status(
                task.task_id, "completed", f"File found on disk at {rel.as_posix()}"
            )

    def _planned_file_is_complete(self, file_path: Path) -> bool:
        """Return True when an on-disk file passes completeness validation."""
        from .code_validator import CodeCompletenessValidator

        result = CodeCompletenessValidator.validate_file(file_path)
        return bool(result.get("complete", True))

    def reconcile_corrupt_completed_files(self, workspace_path: Path) -> int:
        """Mark completed file tasks as failed when on-disk content is truncated or corrupt."""
        if not workspace_path.exists():
            return 0

        from .code_validator import CodeCompletenessValidator

        failed = 0
        for task in self.get_all_tasks():
            if task.task_type != "file_creation":
                continue
            if (task.status or "").lower() != TaskStatus.COMPLETED.value:
                continue
            file_path = (task.metadata or {}).get("file_path", "")
            if not file_path:
                continue

            found = self.resolve_planned_file_on_disk(workspace_path, file_path)
            if found is None:
                continue

            result = CodeCompletenessValidator.validate_file(found)
            if result.get("complete", True):
                continue

            issues = "; ".join(result.get("issues", []))
            logger.warning(
                "Corrupt file task %s (%s): %s",
                task.task_id, file_path, issues,
            )
            self.update_task_status(
                task.task_id,
                "failed",
                f"File {file_path} is incomplete or corrupt: {issues}",
            )
            failed += 1
        return failed

    def finalize_incomplete_tasks(self, workspace_path: Path) -> None:
        """Close out remaining tasks at workflow end with accurate bookkeeping."""
        remaining = self.get_incomplete_tasks()
        if not remaining:
            return

        for task in remaining:
            if task.task_type == "file_creation":
                file_path = (task.metadata or {}).get("file_path", "")
                if not file_path:
                    continue

                found = self.resolve_planned_file_on_disk(workspace_path, file_path)
                if found is not None:
                    if not self._planned_file_is_complete(found):
                        issues = ""
                        from .code_validator import CodeCompletenessValidator
                        check = CodeCompletenessValidator.validate_file(found)
                        issues = "; ".join(check.get("issues", []))
                        logger.info(
                            "Finalize: task %s file at %s failed completeness: %s",
                            task.task_id, file_path, issues,
                        )
                        self.update_task_status(
                            task.task_id,
                            "failed",
                            f"File {file_path} is incomplete or corrupt: {issues}",
                        )
                        continue

                    rel = found.relative_to(workspace_path)
                    logger.info(
                        "Finalize: task %s matched planned file at %s",
                        task.task_id, rel,
                    )
                    self.update_task_status(
                        task.task_id,
                        "completed",
                        f"File found at {rel.as_posix()}",
                    )
                    continue

                if task.status == TaskStatus.FAILED.value:
                    continue

                logger.info(
                    "Finalize: planned file missing for task %s (%s)",
                    task.task_id, file_path,
                )
                self.update_task_status(
                    task.task_id,
                    "failed",
                    f"Planned file {file_path} was not created",
                )
            elif task.task_type == "feature":
                if task.status == TaskStatus.FAILED.value:
                    logger.info(
                        "Finalize: feature task %s remains failed",
                        task.task_id,
                    )
                    continue
                logger.info(
                    "Finalize: feature task %s was not implemented",
                    task.task_id,
                )
                self.update_task_status(
                    task.task_id,
                    "failed",
                    "Feature was planned but not implemented",
                )
            elif task.status == TaskStatus.REGISTERED.value:
                logger.info(
                    "Finalize: task %s (type=%s) never started",
                    task.task_id, task.task_type,
                )
                self.update_task_status(
                    task.task_id,
                    "failed",
                    "Task was planned but never started",
                )
    
    _SOURCE_FILE_EXTENSIONS = frozenset({
        '.java', '.py', '.pyw', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
        '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.scala', '.html', '.css',
        '.vue', '.svelte', '.yaml', '.yml', '.sh', '.bash', '.sql', '.tf', '.hcl',
        '.json'
    })
    _CONFIG_ARTIFACT_NAMES = frozenset({
        'pom.xml', 'build.gradle', 'build.gradle.kts', 'package.json',
        'requirements.txt', 'pyproject.toml', 'go.mod', 'cargo.toml',
        'composer.json', 'gemfile',
    })
    _SCAFFOLDING_PATH_RE = re.compile(
        r"(?:^|/)("
        r"\.github/|\.gitlab/|/workflows/|"
        r"docker-compose|compose\.ya?ml|"
        r"Dockerfile$|Containerfile$|"
        r"\.config\.|eslint|prettier|jest\.config|tsconfig|"
        r"\.env\.|Makefile$|README\.md$"
        r")",
        re.IGNORECASE,
    )
    _TEST_FILE_TIER = 95  # after all source tiers (max 80 + mock 90), registered last
    _TEST_FILE_TIER_TDD = 5  # before models (10) — test-first in full-path TDD

    _STRUCTURE_LAYER_TIERS: Dict[int, str] = {
        10: "data/model/persistence",
        30: "service/business-logic",
        50: "API/controller/handler",
        80: "application entrypoint",
    }
    _LAYER_DIRECTORY_SEGMENTS = frozenset({
        "model", "models", "entity", "entities", "schema", "schemas", "domain",
        "service", "services", "usecase", "business", "logic",
        "controller", "controllers", "handler", "handlers", "view", "views",
        "resource", "resources", "endpoint", "route", "routes", "router", "api",
        "repository", "repositories", "dao", "store", "manager",
        "middleware", "interceptor", "filter", "guard", "auth",
        "serializer", "dto", "mapper", "converter", "config", "util", "utils",
    })

    @staticmethod
    def _is_source_file_path(path: str, description: str = "") -> bool:
        if not path or path.lower() == "unknown":
            return False
            
        # 1. Smart Tagging (Overrides hardcoded extensions)
        desc_upper = description.upper()
        if "[SOURCE]" in desc_upper:
            return True
        if "[CONFIG]" in desc_upper:
            return False
            
        # 2. Fallback to hardcoded extensions if no tag is provided
        lower = path.lower()
        if "/test/" in lower or lower.startswith("test/") or "/tests/" in lower:
            return False
        return any(lower.endswith(ext) for ext in TaskManager._SOURCE_FILE_EXTENSIONS)

    @classmethod
    def _tier_keywords_present(cls, paths: List[str], tier: int) -> bool:
        keywords = cls._FILE_TIERS.get(tier, [])
        for fp in paths:
            fp_lower = fp.lower()
            stem = Path(fp).stem.lower()
            parent = Path(fp).parent.name.lower() if "/" in fp else ""
            for kw in keywords:
                # Exact segment match (original behaviour)
                if kw == stem or kw == parent or kw in fp_lower.split("/"):
                    return True
                # Substring match: e.g. "service" inside "WebSocketService" or "websocket/server"
                if kw in stem or kw in parent:
                    return True
        return False

    @classmethod
    def _has_entrypoint_in_paths(cls, paths: List[str]) -> bool:
        for fp in paths:
            if not cls._is_source_file_path(fp):
                continue
            stem = Path(fp).stem.lower()
            if stem in cls._ENTRYPOINT_NAMES:
                return True
            if cls._classify_file_tier(fp) >= 80:
                return True
        return False

    def _extract_structure_paths(self, tech_stack_content: str) -> List[str]:
        """Extract all file paths and raw directory names from the tech stack content."""
        file_entries = self._extract_files_with_descriptions(tech_stack_content)
        paths = [f["path"] for f in file_entries]

        # Also parse raw block/tree lines to catch directories (e.g. models/, controllers/)
        regions = []
        current_lines = []
        in_block = False
        for line in tech_stack_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if current_lines:
                    regions.append("\n".join(current_lines))
                    current_lines = []
                in_block = not in_block
                continue
            current_lines.append(line)
        if current_lines:
            regions.append("\n".join(current_lines))

        TREE_CHARS_RE = re.compile(r'[├└│─]')
        for block in regions:
            if not TREE_CHARS_RE.search(block):
                continue
            for line in block.splitlines():
                entry_match = re.search(
                    r'[├└│─\s]*([a-zA-Z0-9_.\-][a-zA-Z0-9_/.\-]*/?)(?:\s+#\s*(.*))?',
                    line,
                )
                if entry_match:
                    name = entry_match.group(1).strip()
                    paths.append(name.rstrip('/'))
        return list(set(paths))

    @classmethod
    def _tier_keywords_in_text(cls, text: str, tier: int) -> bool:
        """Check if any keywords of the given tier are present in the text with word boundaries."""
        keywords = cls._FILE_TIERS.get(tier, [])
        for kw in keywords:
            # Word boundary matching
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _is_tier_applicable(self, tier: int, workspace_path: Path, tech_stack_content: Optional[str] = None) -> bool:
        """Determine if a directory/layer tier is applicable to this project based on tech stack or skills."""
        # 1. Parse tech stack (content passed or read from disk)
        if not tech_stack_content:
            tech_stack_file = workspace_path / "tech_stack.md"
            if tech_stack_file.exists():
                try:
                    tech_stack_content = tech_stack_file.read_text(encoding="utf-8")
                except Exception:
                    pass

        if tech_stack_content:
            paths = self._extract_structure_paths(tech_stack_content)
            if self._tier_keywords_present(paths, tier):
                return True

        # 2. Check skill prefetch file
        prefetch_file = workspace_path / "skill_prefetch.json"
        if prefetch_file.exists():
            try:
                data = json.loads(prefetch_file.read_text(encoding="utf-8"))
                all_entries = []
                if isinstance(data, dict):
                    for role_entries in data.values():
                        if isinstance(role_entries, list):
                            all_entries.extend(role_entries)
                for entry in all_entries:
                    content = entry.get("content", "")
                    if content and self._tier_keywords_in_text(content, tier):
                        return True
            except Exception as e:
                logger.warning("Error reading skill_prefetch.json: %s", e)

        return False

    @staticmethod
    def _default_entrypoint_filename(ext: str) -> str:
        ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        defaults = {
            ".java": "Application.java",
            ".kt": "Application.kt",
            ".py": "main.py",
            ".pyw": "main.py",
            ".js": "index.js",
            ".mjs": "index.mjs",
            ".cjs": "index.cjs",
            ".ts": "index.ts",
            ".tsx": "index.tsx",
            ".jsx": "index.jsx",
            ".go": "main.go",
            ".rs": "main.rs",
        }
        return defaults.get(ext, f"main{ext}")

    @staticmethod
    def _java_package_base_from_path(path: str) -> Optional[str]:
        parts = path.replace("\\", "/").split("/")
        if "java" not in parts:
            return None
        java_idx = parts.index("java")
        pkg_parts = parts[java_idx + 1:-1]
        while pkg_parts and pkg_parts[-1].lower() in TaskManager._LAYER_DIRECTORY_SEGMENTS:
            pkg_parts.pop()
        if not pkg_parts:
            return None
        return "/".join(parts[:java_idx + 1] + pkg_parts)

    def _infer_scaffolding_base_and_ext(self, paths: List[str]) -> tuple[Optional[str], str]:
        src_paths = [p for p in paths if self._is_source_file_path(p)]
        if not src_paths:
            return None, ".py"

        exts = [Path(p).suffix.lower() for p in src_paths if "." in p]
        ext = max(set(exts), key=exts.count) if exts else ".py"

        java_bases = [
            self._java_package_base_from_path(p)
            for p in src_paths
            if "java" in p.replace("\\", "/").split("/")
        ]
        java_bases = [b for b in java_bases if b]
        if java_bases:
            return min(java_bases, key=len), ext

        for p in src_paths:
            if p.startswith("src/"):
                return "src", ext

        if "/" in src_paths[0]:
            return str(Path(src_paths[0]).parent), ext
        return None, ext

    def _scaffolding_path_for_tier(self, base: str, ext: str, tier: int) -> str:
        folder_map = {10: "model", 30: "service", 50: "controller"}
        if tier == 80:
            return f"{base}/{self._default_entrypoint_filename(ext)}"
        folder = folder_map.get(tier, "core")
        return f"{base}/{folder}/core{ext}"

    _TIER_SCaffolding_DESCRIPTIONS = {
        10: "Data/persistence layer — entity, model, or schema definition",
        30: "Business logic layer — service or use-case implementation",
        50: "API/request handling layer — controller, handler, or route module",
        80: "Application entrypoint that starts the runtime",
    }

    def detect_workspace_structure_gaps(self, workspace: Path) -> List[str]:
        """Return human-readable gap prompts for missing structural layers on disk."""
        paths: List[str] = []
        for p in workspace.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(workspace)).replace("\\", "/")
            if rel.startswith("features/") or rel.endswith(".md"):
                continue
            if self._is_source_file_path(rel):
                paths.append(rel)

        gaps: List[str] = []
        for tier, label in self._STRUCTURE_LAYER_TIERS.items():
            if tier == 80:
                if self._has_entrypoint_in_paths(paths):
                    continue
            else:
                if self._tier_keywords_present(paths, tier):
                    continue

            if self._is_tier_applicable(tier, workspace):
                if tier == 80:
                    gaps.append(
                        "Create the missing application entrypoint/bootstrap file following "
                        "your tech stack conventions."
                    )
                else:
                    gaps.append(
                        f"Create missing {label} layer file(s) following your tech stack conventions."
                    )
        return gaps

    @staticmethod
    def _is_scaffolding_path(path: str) -> bool:
        """Build/CI/config paths — not implementation sources the dev agent implements."""
        if not path:
            return True
        lower = path.lower().replace("\\", "/")
        if lower in TaskManager._CONFIG_ARTIFACT_NAMES:
            return True
        return bool(TaskManager._SCAFFOLDING_PATH_RE.search(lower))

    @classmethod
    def _implementation_source_paths(
        cls,
        file_entries: List[Dict[str, str]],
    ) -> List[str]:
        """Source files that represent application logic, not scaffolding."""
        impl: List[str] = []
        for entry in file_entries:
            path = entry.get("path", "")
            desc = entry.get("description", "")
            if not cls._is_source_file_path(path, desc):
                continue
            if cls._is_scaffolding_path(path):
                continue
            impl.append(path)
        return impl

    def validate_tech_stack_completeness(
        self,
        tech_stack_content: str,
        *,
        design_spec: str = "",
        solution_spec: str = "",
    ) -> Dict[str, Any]:
        """
        Validate that the tech stack lists enough concrete implementation files.

        Thresholds are derived from named components in design/solution specs —
        not from hardcoded framework rules.
        """
        if not tech_stack_content or not tech_stack_content.strip():
            return {
                "valid": False,
                "issues": ["tech_stack.md is empty. The Tech Architect must enumerate the project file structure."],
            }

        file_entries = self._extract_files_with_descriptions(tech_stack_content)
        all_paths = [f["path"] for f in file_entries]
        src_files = [
            f["path"] for f in file_entries
            if self._is_source_file_path(f["path"], f.get("description", ""))
        ]
        impl_files = self._implementation_source_paths(file_entries)

        issues: List[str] = []

        if len(src_files) == 0:
            issues.append(
                "No concrete source files found in tech_stack.md. "
                "List the project file structure with real filenames and extensions "
                "(e.g. src/main.py, com/example/App.java). "
                "Do NOT write file contents — just the tree structure."
            )

        components = extract_named_components(solution_spec) or extract_named_components(design_spec)
        min_impl = max(4, len(components) * 2) if components else 4

        if len(impl_files) < min_impl:
            issues.append(
                f"File tree is too shallow: found {len(impl_files)} implementation "
                f"source file(s) but need at least {min_impl} "
                f"(derived from {len(components) or 'default'} named component(s)). "
                "Enumerate concrete source files for every module/service — not "
                "directory-only entries or CI/config scaffolding."
            )

        if components:
            missing = [
                name for name in components
                if not component_reflected_in_artifact(name, tech_stack_content, all_paths)
            ]
            if len(missing) > len(components) // 2:
                issues.append(
                    "File tree does not cover named components from the design/solution "
                    f"spec. Missing or unrepresented: {missing[:8]}"
                    + ("..." if len(missing) > 8 else "")
                    + ". Each component needs its own directory with concrete source files."
                )

        if issues:
            return {"valid": False, "issues": issues}

        return {"valid": True, "issues": [], "implementation_files": len(impl_files)}

    
    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get current status of a task"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        return TaskStatus(row[0]) if row else None
    
    def get_pending_tasks(self) -> List[TaskDefinition]:
        """Get list of tasks that haven't been created yet"""
        return self._get_tasks_by_status(TaskStatus.REGISTERED)
    
    def get_registered_file_paths(self) -> set:
        """Return the set of file_path values from all file_creation tasks."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT metadata FROM tasks WHERE project_id = ? AND task_type = 'file_creation'",
            (self.project_id,),
        )
        paths: set = set()
        for (raw,) in cursor.fetchall():
            if raw:
                try:
                    meta = json.loads(raw)
                    fp = meta.get("file_path", "")
                    if fp:
                        paths.add(fp)
                except Exception:
                    pass
        conn.close()
        return paths

    def get_task_by_id(self, task_id: str) -> Optional[TaskDefinition]:
        """Return a single task by its ID, or None if not found."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT task_id, phase, task_type, description, required, source, status, metadata "
            "FROM tasks WHERE task_id = ?", (task_id,),
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return TaskDefinition(
            task_id=row["task_id"],
            phase=row["phase"],
            task_type=row["task_type"],
            description=row["description"] or "",
            required=bool(row["required"]),
            source=row["source"],
            status=row["status"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def get_incomplete_tasks(self) -> List[TaskDefinition]:
        """Get list of tasks that were created but not completed"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT task_id, phase, task_type, description, required, source, status, metadata
            FROM tasks 
            WHERE project_id = ? AND required = 1 
            AND status NOT IN (?, ?)
        """, (self.project_id, TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append(TaskDefinition(
                task_id=row[0],
                phase=row[1],
                task_type=row[2],
                description=row[3] or "",
                required=bool(row[4]),
                source=row[5],
                status=row[6],
                metadata=json.loads(row[7]) if row[7] else None
            ))
        conn.close()
        return tasks
    
    def get_task_history(self, task_id: str) -> List[Dict]:
        """Get execution history for a task"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT event_type, event_data, timestamp 
            FROM task_execution_log 
            WHERE task_id = ? 
            ORDER BY timestamp
        """, (task_id,))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                'event_type': row[0],
                'event_data': json.loads(row[1]) if row[1] else {},
                'timestamp': row[2]
            })
        conn.close()
        return history
    
    def _update_task_status(self, task_id: str, status: TaskStatus):
        """Internal method to update task status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks 
            SET status = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE task_id = ?
        """, (status.value, task_id))
        conn.commit()
        conn.close()
    
    def _log_event(self, task_id: str, event_type: str, event_data: Dict):
        """Log task execution event"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO task_execution_log (task_id, event_type, event_data)
            VALUES (?, ?, ?)
        """, (task_id, event_type, json.dumps(event_data)))
        conn.commit()
        conn.close()
    
    def get_all_tasks(self) -> List[TaskDefinition]:
        """Get all tasks registered for the project"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT task_id, phase, task_type, description, required, source, status, metadata
            FROM tasks 
            WHERE project_id = ?
            ORDER BY created_at ASC
        """, (self.project_id,))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append(TaskDefinition(
                task_id=row[0],
                phase=row[1],
                task_type=row[2],
                description=row[3] or "",
                required=bool(row[4]),
                source=row[5],
                status=row[6],
                metadata=json.loads(row[7]) if row[7] else None
            ))
        conn.close()
        return tasks

    def _get_tasks_by_status(self, status: TaskStatus) -> List[TaskDefinition]:
        """Get tasks by status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT task_id, phase, task_type, description, required, source, status, metadata
            FROM tasks 
            WHERE project_id = ? AND status = ?
        """, (self.project_id, status.value))
        
        tasks = []
        for row in cursor.fetchall():
            tasks.append(TaskDefinition(
                task_id=row[0],
                phase=row[1],
                task_type=row[2],
                description=row[3] or "",
                required=bool(row[4]),
                source=row[5],
                status=row[6],
                metadata=json.loads(row[7]) if row[7] else None
            ))
        conn.close()
        return tasks

    def update_task_status_by_output(self, output: str):
        """
        Update task status based on agent output.
        Scans output for file creation markers like '✅ Successfully wrote to <path>'
        or '✅ Created <path>'.
        """
        import re
        from pathlib import Path
        
        logger.debug(f"Scanning output for task updates: {output[:200]}...")
        
        # Look for file creation markers
        patterns = [
            r"Successfully wrote to ([a-zA-Z0-9_\-\.\/]+)",
            r"Created ([a-zA-Z0-9_\-\.\/]+)",
            r"✅ Created ([a-zA-Z0-9_\-\.\/]+)",
            r"✅ Successfully wrote to ([a-zA-Z0-9_\-\.\/]+)"
        ]
        
        found_any = False
        # Get all registered/created tasks to use for fallback matching
        all_tasks = self.get_all_tasks()
        pending_tasks = [t for t in all_tasks if t.status in (TaskStatus.REGISTERED.value, TaskStatus.CREATED.value)]
        
        for pattern in patterns:
            matches = re.findall(pattern, output)
            for file_path in matches:
                # Clean up file path
                file_path = file_path.strip().rstrip('.').rstrip(')')
                
                # 1. Try exact task ID match
                task_id = f"file_{self.normalize_file_path_for_task_id(file_path)}"
                if any(t.task_id == task_id for t in all_tasks):
                    logger.info(f"🎯 Found exact completion marker for task: {task_id} (file: {file_path})")
                    self.update_task_status(task_id, "completed", f"File created: {file_path}")
                    found_any = True
                    continue

                # 2. Match pending tasks by exact planned path or suffix (reorganization)
                output_posix = Path(file_path).as_posix()
                for task in pending_tasks:
                    task_file_path = (task.metadata or {}).get("file_path", "")
                    if not task_file_path:
                        continue

                    task_posix = Path(task_file_path).as_posix()
                    if output_posix == task_posix or output_posix.endswith("/" + task_posix):
                        logger.info(
                            "Found completion marker for task: %s "
                            "(output %s matches planned %s)",
                            task.task_id, file_path, task_file_path,
                        )
                        self.update_task_status(
                            task.task_id,
                            "completed",
                            f"File created: {file_path} (matched via {task_file_path})",
                        )
                        found_any = True
                        break
        
        if not found_any:
            logger.debug("No file creation markers found in output.")

    def update_task_status(self, task_id: str, status: str, error_message: str = None):
        """Update task status by ID"""
        try:
            task_status = TaskStatus(status.lower())
            self.mark_task_executed(task_id, task_status, error_message)
        except ValueError:
            logger.error(f"Invalid task status: {status}")

    def reset_tasks_for_retry(self, task_ids: List[str]) -> int:
        """Reset tasks to registered so they can be claimed again by get_next_actionable_task."""
        if not task_ids:
            return 0
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        reset = 0
        reset_ids: List[str] = []
        for task_id in task_ids:
            cursor.execute(
                """
                UPDATE tasks
                SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP,
                    started_at = NULL, completed_at = NULL
                WHERE task_id = ? AND project_id = ?
                """,
                (
                    TaskStatus.REGISTERED.value,
                    "Reset for retry",
                    task_id,
                    self.project_id,
                ),
            )
            if cursor.rowcount:
                reset += 1
                reset_ids.append(task_id)
        conn.commit()
        conn.close()
        for task_id in reset_ids:
            self._log_event(task_id, "reset_for_retry", {})
        logger.info("Reset %d task(s) for retry", reset)
        return reset

    def normalize_file_path_for_task_id(self, file_path: str) -> str:
        """Normalize file path for use in task ID"""
        # Remove leading src/ or tests/ if present to match tech_stack extraction logic
        normalized = file_path
        if normalized.startswith('src/'):
            normalized = normalized[4:]
        elif normalized.startswith('tests/'):
            normalized = normalized[6:]
            
        return normalized.replace('/', '_').replace('.', '_').replace('-', '_')

    # ── Granular task decomposition ──────────────────────────────────────────

    def register_granular_tasks(
        self,
        design_spec: str,
        tech_stack_content: str,
        *,
        tdd: bool = False,
    ) -> List[TaskDefinition]:
        """Decompose design spec + tech stack into per-file tasks with domain context.

        Parses the file structure from tech_stack_content and cross-references
        bounded contexts from design_spec to attach domain context to each task.
        Model/core files are registered before views/controllers to express dependencies.
        File descriptions from tree comments are preserved in metadata.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM tasks WHERE project_id = ? AND task_type = 'file_creation' AND source IN ('tech_stack', ?)",
            (self.project_id, str(self.db_path.parent / "tech_stack.md")),
        )
        conn.commit()
        conn.close()

        self._tdd_mode = bool(tdd)

        file_entries = self._extract_files_with_descriptions(tech_stack_content)
        contexts = self._extract_bounded_contexts(design_spec)
        feature_contents = self._load_workspace_feature_files()

        raw_tasks: List[TaskDefinition] = []

        for entry in file_entries:
            fp = entry["path"]
            basename = Path(fp).name if fp else ""
            if not _is_valid_file_path(basename):
                logger.warning("Skipping invalid file path from tech stack: %r", fp)
                continue
            file_desc = entry.get("description", "")
            task_id = f"file_{self.normalize_file_path_for_task_id(fp)}"
            domain = self._match_domain_context(fp, contexts)

            task = TaskDefinition(
                task_id=task_id,
                phase="development",
                task_type="file_creation",
                description=f"Create file: {fp}",
                source="tech_stack",
                metadata={
                    "file_path": fp,
                    "domain_context": domain,
                    "file_description": file_desc,
                    "feature_files": self._match_feature_files(fp, feature_contents),
                },
            )
            raw_tasks.append(task)

        # Auto-inject __init__.py for Python projects
        raw_tasks = self._inject_init_py_tasks(raw_tasks)

        # Inject framework scaffolding tasks
        raw_tasks = self._inject_framework_scaffolding_tasks(raw_tasks, tech_stack_content, design_spec)

        # Sort tasks by tier so lower-tier files are registered first
        raw_tasks.sort(key=lambda t: self._classify_file_tier(
            (t.metadata or {}).get("file_path", ""),
            tdd=tdd,
        ))

        registered: List[TaskDefinition] = []
        for t in raw_tasks:
            deps = self._infer_dependencies(t, registered)
            t.dependencies = deps
            self.register_task(t)
            registered.append(t)

        all_tasks = registered
        logger.info("Registered %d granular tasks (sorted by %d tiers)",
                     len(all_tasks), len(set(
                         self._classify_file_tier((t.metadata or {}).get("file_path", ""))
                         for t in all_tasks
                     )))

        if tdd:
            plan_path = self.db_path.parent / "test_plan.md"
            plan_content = ""
            if plan_path.is_file():
                try:
                    plan_content = plan_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
            test_tasks = self.register_tdd_test_tasks(
                tech_stack_content,
                plan_content,
            )
            all_tasks = registered + test_tasks

        self.generate_implementation_plan_file(all_tasks)
        return all_tasks

    def register_tdd_test_tasks(
        self,
        tech_stack_content: str = "",
        test_plan_content: str = "",
    ) -> List[TaskDefinition]:
        """Register missing test file_creation tasks derived from source tree + test plan."""
        self._tdd_mode = True

        source_paths: List[str] = []
        for t in self.get_all_tasks():
            if t.task_type != "file_creation":
                continue
            fp = (t.metadata or {}).get("file_path", "")
            if fp and not is_test_file_path(fp):
                source_paths.append(fp)

        if not source_paths and tech_stack_content:
            source_paths = [
                e["path"]
                for e in self._extract_files_with_descriptions(tech_stack_content)
            ]

        existing_paths = {
            (t.metadata or {}).get("file_path", "")
            for t in self.get_all_tasks()
            if t.task_type == "file_creation"
        }

        test_paths = derive_tdd_test_paths(source_paths, test_plan_content)
        registered: List[TaskDefinition] = []
        prior_tasks = list(self.get_all_tasks())

        for fp in test_paths:
            if fp in existing_paths:
                continue
            basename = Path(fp).name
            if not _is_valid_file_path(basename):
                continue
            task_id = f"file_{self.normalize_file_path_for_task_id(fp)}"
            task = TaskDefinition(
                task_id=task_id,
                phase="development",
                task_type="file_creation",
                description=f"Create test file: {fp}",
                source="test_plan",
                metadata={
                    "file_path": fp,
                    "domain_context": "",
                    "file_description": "TDD test module (derived from source tree or test plan)",
                },
            )
            deps = self._infer_dependencies(task, prior_tasks + registered)
            task.dependencies = deps
            self.register_task(task)
            registered.append(task)
            existing_paths.add(fp)

        if registered:
            logger.info(
                "Registered %d TDD test file task(s) from source tree / test plan",
                len(registered),
            )
        return registered

    def generate_implementation_plan_file(self, tasks: List[TaskDefinition]) -> None:
        """Generate implementation_plan.md in the project workspace."""
        plan_path = self.db_path.parent / "implementation_plan.md"
        
        # If the file already exists (e.g. written by the Tech Architect agent),
        # we preserve its content and optionally append the deconstructed file task list
        # as a reference appendix if not already present.
        existing_content = ""
        if plan_path.exists():
            try:
                existing_content = plan_path.read_text(encoding="utf-8")
            except Exception:
                pass
                
        # If it has a rich agent-written implementation plan, we preserve it.
        # We append the detailed tasks appendix at the end if it doesn't already contain it.
        if existing_content and "### Appendix: Deconstructed Task List" in existing_content:
            # Already has our appendix or was generated by this function previously.
            return
            
        content = []
        if existing_content:
            content.append(existing_content)
            content.append("\n\n---\n")
            content.append("### Appendix: Deconstructed Task List\n")
            content.append("This appendix lists the deconstructed code-generation and validation tasks registered for execution.\n")
        else:
            content.append("# Project Implementation Plan\n")
            content.append("This implementation plan details the deconstructed tasks, file creation order, and verification steps necessary to implement the project requirements.\n")
            
        content.append("#### Task Summary\n")
        content.append(f"Total registered tasks: **{len(tasks)}**\n")
        
        # Group tasks by phase or type
        phases = {}
        for t in tasks:
            phase_name = t.phase.replace("_", " ").title()
            if phase_name not in phases:
                phases[phase_name] = []
            phases[phase_name].append(t)
            
        for phase_name, phase_tasks in phases.items():
            content.append(f"##### Phase: {phase_name}\n")
            for t in phase_tasks:
                file_path = (t.metadata or {}).get("file_path")
                file_desc = (t.metadata or {}).get("file_description", "")
                desc_str = f" (`{file_path}`)" if file_path else ""
                desc_detail = f" - *{file_desc}*" if file_desc else ""
                
                content.append(f"- **{t.task_id}**: {t.description}{desc_str}{desc_detail}")
                if t.dependencies:
                    deps_str = ", ".join(t.dependencies)
                    content.append(f"  *Dependencies: {deps_str}*")
            content.append("")
            
        if not existing_content:
            content.append("## Verification Steps\n")
            content.append("1. **Syntax Checks**: Verify that all files parse correctly without syntax errors.")
            content.append("2. **Import Integrity**: Check that there are no broken relative or module imports.")
            content.append("3. **API Contract Verification**: Ensure routes conform to the designated contract.")
            content.append("4. **Functional Testing**: Run pytest or equivalent tests where configured.")
            content.append("\n")
        
        try:
            plan_path.write_text("\n".join(content), encoding="utf-8")
            logger.info("Generated implementation_plan.md at %s", plan_path)
        except Exception as e:
            logger.warning("Could not write implementation_plan.md: %s", e)

    def _inject_init_py_tasks(self, tasks: List[TaskDefinition]) -> List[TaskDefinition]:
        """Auto-inject ``__init__.py`` creation tasks for Python package directories.

        Scans registered file paths for ``.py`` files inside sub-directories and
        ensures every intermediate directory gets an ``__init__.py`` task.

        Skips a directory if a same-named flat module file already exists
        (e.g. won't create ``app/models/__init__.py`` when ``app/models.py``
        is already in the task list — that would be a file/package conflict).
        """
        from ..utils.manifest_guard import expand_python_package_inits

        existing_paths = {(t.metadata or {}).get("file_path", "") for t in tasks}
        py_files = [p for p in existing_paths if p.endswith(".py")]
        if not py_files:
            return tasks

        needed_inits = {
            p for p in expand_python_package_inits(existing_paths)
            if p.endswith("/__init__.py") and p not in existing_paths
        }

        for init_path in sorted(needed_inits):
            task = TaskDefinition(
                task_id=f"file_{self.normalize_file_path_for_task_id(init_path)}",
                phase="development",
                task_type="file_creation",
                description=f"Create file: {init_path}",
                source="auto_injected",
                metadata={
                    "file_path": init_path,
                    "domain_context": "",
                    "file_description": "Python package init (auto-generated)",
                    "auto_content": "",
                },
            )
            tasks.append(task)
            existing_paths.add(init_path)
            logger.info("Auto-injected __init__.py task: %s", init_path)

        return tasks

    def _inject_framework_scaffolding_tasks(
        self, tasks: List[TaskDefinition], tech_stack_content: str, design_spec: str
    ) -> List[TaskDefinition]:
        """Inject missing structural-layer files by reading scaffolding trees from skills."""
        existing_paths = {(t.metadata or {}).get("file_path", "") for t in tasks}
        
        workspace_path = self.db_path.parent
        prefetch_file = workspace_path / "skill_prefetch.json"
        skill_scaffold_paths = set()
        
        if prefetch_file.exists():
            try:
                import json
                data = json.loads(prefetch_file.read_text(encoding="utf-8"))
                all_entries = []
                if isinstance(data, dict):
                    for role_entries in data.values():
                        if isinstance(role_entries, list):
                            all_entries.extend(role_entries)
                
                for entry in all_entries:
                    content = entry.get("content", "")
                    if content:
                        # Extract any ASCII tree structures present in the skill documentation
                        paths = self._extract_structure_paths(content)
                        skill_scaffold_paths.update(paths)
            except Exception as e:
                logger.warning("Error parsing skills for scaffolding: %s", e)

        contexts = self._extract_bounded_contexts(design_spec)
        
        # Inject the exact files defined in the framework skills
        for fp in skill_scaffold_paths:
            if not self._is_source_file_path(fp):
                continue
            if fp in existing_paths:
                continue
                
            task_id = f"file_{self.normalize_file_path_for_task_id(fp)}"
            domain = self._match_domain_context(fp, contexts)
            task = TaskDefinition(
                task_id=task_id,
                phase="development",
                task_type="file_creation",
                description=f"Create framework scaffolding file: {fp}",
                source="skill_scaffolding",
                metadata={
                    "file_path": fp,
                    "domain_context": domain,
                    "file_description": "Framework scaffolding file derived from skills",
                },
            )
            tasks.append(task)
            existing_paths.add(fp)
            logger.info("Auto-injected skill-based scaffolding task: %s", fp)

        return tasks

    def _extract_files_from_content(self, content: str) -> List[str]:
        """Extract file paths from a tech_stack markdown string, preserving directory hierarchy."""
        entries = self._extract_files_with_descriptions(content)
        return [e["path"] for e in entries]

    def _extract_files_with_descriptions(self, content: str) -> List[Dict[str, str]]:
        """Extract file paths with descriptions from a tree structure in markdown code blocks.

        Tracks indentation to reconstruct full paths like ``api/models.py``
        instead of just ``models.py``.  Also captures inline comments
        (e.g. ``# Database connection``) as descriptions.

        The root project directory (first entry ending with ``/``) is stripped
        from paths so files are relative to the project root.

        Uses a two-pass approach: first identifies all code blocks, then only
        parses those that contain tree characters (├, └, │, ─).  This avoids
        toggling issues with nested backtick blocks.
        """
        # ── Pass 1: split into regions (code blocks and gaps between them) ─
        regions: List[str] = []
        current_lines: List[str] = []
        in_block = False

        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                # Save accumulated lines as a region before toggling
                if current_lines:
                    regions.append("\n".join(current_lines))
                    current_lines = []
                in_block = not in_block
                continue
            current_lines.append(line)
        if current_lines:
            regions.append("\n".join(current_lines))

        # ── Pass 2: parse regions that contain tree characters ───────────
        TREE_CHARS_RE = re.compile(r'[├└│─]')
        entries: List[Dict[str, str]] = []

        for block in regions:
            if not TREE_CHARS_RE.search(block):
                continue

            dir_stack: List[tuple] = []
            root_dir: Optional[str] = None

            for line in block.splitlines():
                tree_chars = re.match(r'^([\s│]*[├└─\s]*)', line)
                indent = len(tree_chars.group(1).replace('│', ' ').replace('├', ' ')
                             .replace('└', ' ').replace('─', ' ')) if tree_chars else 0

                entry_match = re.search(
                    r'[├└│─\s]*([a-zA-Z0-9_.\-][a-zA-Z0-9_/.\-]*/?)(?:\s+#\s*(.*))?',
                    line,
                )
                if not entry_match:
                    continue

                name = entry_match.group(1).strip()
                description = (entry_match.group(2) or "").strip()

                while dir_stack and dir_stack[-1][0] >= indent:
                    dir_stack.pop()

                if name.endswith('/'):
                    dir_name = name.rstrip('/')
                    if root_dir is None and not dir_stack:
                        root_dir = dir_name
                    dir_stack.append((indent, dir_name))
                elif _is_valid_file_path(name):
                    prefix = "/".join(d[1] for d in dir_stack)
                    full_path = f"{prefix}/{name}" if prefix else name
                    if root_dir and full_path.startswith(root_dir + "/"):
                        full_path = full_path[len(root_dir) + 1:]
                    entries.append({"path": full_path, "description": description})

        # ── Pass 3: plain path lists (one path per line, no tree chars) ──
        # Tech architect pass 2 often outputs this format instead of unicode trees.
        _PLAIN_PATH_RE = re.compile(
            r'^([a-zA-Z0-9_.\-][a-zA-Z0-9_/.\-]*\.[a-zA-Z0-9]+)(?:\s+#\s*(.*))?\s*$'
        )
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("```"):
                continue
            if stripped.startswith("#") and not _PLAIN_PATH_RE.match(stripped.lstrip("#").strip()):
                continue
            match = _PLAIN_PATH_RE.match(stripped)
            if match and _is_valid_file_path(match.group(1)):
                entries.append({
                    "path": match.group(1).strip(),
                    "description": (match.group(2) or "").strip(),
                })

        seen: set = set()
        deduped: List[Dict[str, str]] = []
        for e in entries:
            if e["path"] not in seen:
                seen.add(e["path"])
                deduped.append(e)
        return deduped

    def scaffold_directories_from_tech_stack(
        self,
        tech_stack_content: str,
        workspace_path: Path,
    ) -> List[str]:
        """Create empty parent directories for every file in the tech_stack tree."""
        workspace_path = Path(workspace_path)
        created: List[str] = []
        seen_dirs: set[str] = set()
        for entry in self._extract_files_with_descriptions(tech_stack_content):
            rel = (entry.get("path") or "").strip()
            if not rel:
                continue
            parent = (workspace_path / rel).parent
            if parent == workspace_path or str(parent) in seen_dirs:
                continue
            if not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
                seen_dirs.add(str(parent))
                try:
                    created.append(str(parent.relative_to(workspace_path)))
                except ValueError:
                    created.append(str(parent))
        if created:
            logger.info(
                "Scaffolded %d director(ies) from tech_stack tree under %s",
                len(created),
                workspace_path,
            )
        return created

    def _extract_bounded_contexts(self, design_spec: str) -> Dict[str, str]:
        """Extract bounded context name -> description from design spec."""
        contexts: Dict[str, str] = {}
        pattern = re.compile(
            r'\*\*([^*]+)\*\*\s*[-–—:]\s*(.*)',
            re.IGNORECASE,
        )
        for m in pattern.finditer(design_spec):
            name = m.group(1).strip()
            desc = m.group(2).strip()
            contexts[name.lower()] = f"{name}: {desc}"
        return contexts

    def _match_domain_context(self, file_path: str, contexts: Dict[str, str]) -> str:
        """Match a file path to the best bounded context.

        Matches against both the context key and full description, and also
        checks if the file stem appears in the description (e.g. 'flight' in
        'Flight availability tracking').
        """
        fp_lower = file_path.lower()
        file_stem = Path(file_path).stem.lower().replace("_", " ")
        stem_words = set(re.findall(r'\w+', file_stem))

        best = ""
        best_score = 0
        for key, desc in contexts.items():
            desc_lower = desc.lower()
            key_words = set(re.findall(r'\w+', key))
            desc_words = set(re.findall(r'\w+', desc_lower))
            all_words = key_words | desc_words

            score = sum(1 for kw in all_words if kw in fp_lower)
            score += sum(2 for sw in stem_words if sw in desc_lower)

            if score > best_score:
                best_score = score
                best = desc
        return best

    _JS_TS_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})

    @staticmethod
    def _detect_module_system(tech_stack: Optional[str]) -> Optional[str]:
        """Extract the declared module system from tech stack text.

        Returns "esm", "commonjs", "script-tags", or None.
        """
        if not tech_stack:
            return None
        lower = tech_stack.lower()
        if "es modules" in lower or "es module" in lower:
            return "esm"
        if "commonjs" in lower:
            return "commonjs"
        if "script tag" in lower or "script-tag" in lower:
            return "script-tags"
        return None

    _MODULE_SYSTEM_INSTRUCTIONS: Dict[str, List[str]] = {
        "esm": [
            "MODULE SYSTEM: This project uses ES modules.",
            "- Use `import`/`export` syntax in ALL .js/.ts files.",
            "- Do NOT use `require()` or `module.exports`.",
            "- HTML files must load scripts with `<script type=\"module\">`.",
        ],
        "commonjs": [
            "MODULE SYSTEM: This project uses CommonJS.",
            "- Use `require()` and `module.exports` in ALL .js files.",
            "- Do NOT use `import`/`export` statements.",
        ],
        "script-tags": [
            "MODULE SYSTEM: This project uses plain script tags (no module system).",
            "- Do NOT use `import`/`export` or `require()`.",
            "- Expose functionality via global variables or the `window` object.",
            "- HTML loads scripts with plain `<script src=\"...\">` tags.",
        ],
    }

    _TEST_FRAMEWORK_RULES: Dict[str, List[str]] = {
        "vanilla-jest": [
            "TEST IMPORTS — This is a Vanilla JS project tested with Jest + jsdom.",
            "- Import from 'jest' globals (describe, test, expect) — do NOT import them.",
            "- Do NOT import @testing-library/react, @testing-library/jest-dom, or any React packages.",
            "- Do NOT import 'cheerio', 'enzyme', or other React/Vue/Angular test utilities.",
            "- You MAY import '@jest/globals' if needed for TypeScript types.",
            "- Import source modules using correct relative paths from the PROJECT FILE TREE below.",
        ],
        "react-jest": [
            "TEST IMPORTS — This is a React project tested with Jest.",
            "- You MAY import from @testing-library/react, @testing-library/jest-dom.",
            "- Import React components using correct relative paths from the PROJECT FILE TREE below.",
        ],
        "pytest": [
            "TEST IMPORTS — This is a Python project tested with pytest.",
            "- Import pytest. Do NOT import unittest unless the tech stack specifies it.",
            "- Import source modules using correct relative paths.",
        ],
        "junit": [
            "TEST IMPORTS — This is a Java project tested with JUnit.",
            "- Import org.junit.jupiter.api.* for JUnit 5.",
            "- Import source classes using the correct package paths.",
        ],
    }

    @staticmethod
    def _detect_test_framework(tech_stack: str) -> Optional[str]:
        """Identify the test framework from tech stack text."""
        if not tech_stack:
            return None
        lower = tech_stack.lower()
        if "react" in lower and ("jest" in lower or "testing-library" in lower):
            return "react-jest"
        if "jest" in lower or "jsdom" in lower:
            return "vanilla-jest"
        if "pytest" in lower:
            return "pytest"
        if "junit" in lower:
            return "junit"
        return None

    _ENTRYPOINT_NAMES = frozenset({
        "app", "main", "server", "index", "wsgi", "asgi", "application", "bootstrap",
    })

    _FRAMEWORK_HINTS: Dict[str, List[str]] = {
        "flask": [
            "Create the Flask app instance with Flask(__name__)",
            "Configure SQLAlchemy: set SQLALCHEMY_DATABASE_URI and call db.init_app(app) or pass app to SQLAlchemy(app)",
            "Import and register your route module(s) so all @app.route endpoints are loaded",
            "Add an if __name__ == '__main__' block that calls app.run()",
        ],
        "fastapi": [
            "Create the FastAPI instance with FastAPI()",
            "Import and include routers via app.include_router()",
            "Add startup event for database connection if needed",
        ],
        "express": [
            "Create the Express app with express()",
            "Register middleware (body parser, cors, etc.)",
            "Import and mount route modules with app.use()",
            "Call app.listen(port) to start the server",
        ],
        "django": [
            "Ensure INSTALLED_APPS includes all project apps",
            "Configure DATABASES with the correct engine",
            "Set ROOT_URLCONF to point to the URL configuration module",
        ],
        "spring": [
            "Annotate the class with @SpringBootApplication",
            "Include SpringApplication.run(Application.class, args) in main()",
            "Ensure @ComponentScan picks up all controller/service packages",
        ],
    }

    def _get_entrypoint_hints(self, file_path: str, tech_stack: str) -> List[str]:
        """Return framework-specific wiring hints if the file is an entrypoint."""
        stem = Path(file_path).stem.lower()
        if stem not in self._ENTRYPOINT_NAMES:
            return []

        lower_stack = tech_stack.lower()
        for fw, hints in self._FRAMEWORK_HINTS.items():
            if fw in lower_stack:
                return hints
        return []

    _IMPORTABLE_EXTENSIONS = frozenset({
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    })

    @staticmethod
    def _compute_import_hints(file_path: str, all_paths: List[str], max_hints: int = 15) -> List[str]:
        """Compute correct relative import paths from *file_path* to other project files.

        Returns lines like:
          ``import { Foo } from './controllers/FooController'  (for backend/src/server.ts -> backend/src/controllers/FooController.ts)``
        """
        from pathlib import PurePosixPath

        file_p = PurePosixPath(file_path)
        file_dir = file_p.parent
        file_ext = file_p.suffix.lower()

        importable_exts = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt"}
        if file_ext not in importable_exts:
            return []

        hints: List[str] = []
        for target in all_paths:
            target_p = PurePosixPath(target)
            if target == file_path or target_p.suffix.lower() not in importable_exts:
                continue
            # Skip test files from hints (they don't get imported by source)
            target_name = target_p.name.lower()
            if ("test" in target_name or "spec" in target_name) and file_ext != ".test.ts":
                continue

            try:
                rel = PurePosixPath(os.path.relpath(str(target_p), str(file_dir)))
            except ValueError:
                continue

            rel_str = str(rel)
            if not rel_str.startswith("."):
                rel_str = "./" + rel_str

            if file_ext in {".ts", ".tsx", ".js", ".jsx"}:
                # Strip extension for JS/TS imports
                for ext in (".ts", ".tsx", ".js", ".jsx"):
                    if rel_str.endswith(ext):
                        rel_str = rel_str[: -len(ext)]
                        break
                hints.append(f"{target_p.stem}: import from '{rel_str}'")
            elif file_ext == ".py":
                module_path = rel_str.replace("/", ".").replace("\\", ".")
                if module_path.endswith(".py"):
                    module_path = module_path[:-3]
                if module_path.startswith(".."):
                    continue
                hints.append(f"{target_p.stem}: from {module_path} import ...")

            if len(hints) >= max_hints:
                break

        return hints

    _ROUTE_FILE_KEYWORDS = {"route", "routes", "router", "controller", "controllers",
                            "handler", "handlers", "endpoint", "api", "resource", "view", "views"}
    _CLIENT_FILE_KEYWORDS = {"api", "client", "service", "services", "fetch", "http", "axios"}
    _FRONTEND_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}

    def _format_api_contract_for_file(
        self, file_path: str, contract: Dict[str, Any],
    ) -> str:
        """Format relevant API contract endpoints for inclusion in a file prompt.

        For backend route files: shows which endpoints this file must implement.
        For frontend client/service files: shows which endpoints are available to call.
        For other files: returns empty string (contract not relevant).
        """
        if not contract or not isinstance(contract, dict):
            return ""

        paths = contract.get("paths", {})
        if not paths:
            return ""

        fp_lower = file_path.lower()
        stem = Path(file_path).stem.lower()
        parent = Path(file_path).parent.name.lower() if "/" in file_path else ""
        ext = Path(file_path).suffix.lower()

        is_route_file = stem in self._ROUTE_FILE_KEYWORDS or parent in self._ROUTE_FILE_KEYWORDS
        is_frontend_client = ext in self._FRONTEND_EXTENSIONS and (
            stem in self._CLIENT_FILE_KEYWORDS or parent in self._CLIENT_FILE_KEYWORDS
        )

        if not is_route_file and not is_frontend_client:
            return ""

        lines = []
        if is_route_file:
            lines.append("API CONTRACT — This file must IMPLEMENT these endpoints:")
        else:
            lines.append("API CONTRACT — These backend endpoints are available to call:")

        for path_str, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, details in methods.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                summary = ""
                if isinstance(details, dict):
                    summary = details.get("summary", details.get("description", ""))
                    op_id = details.get("operationId", "")
                    if op_id:
                        summary = f"{op_id}: {summary}" if summary else op_id
                lines.append(f"  {method.upper()} {path_str}" + (f" — {summary}" if summary else ""))

        schemas = contract.get("components", {}).get("schemas", {})
        if schemas:
            lines.append("")
            lines.append("Schemas (request/response shapes):")
            for schema_name, schema_def in schemas.items():
                props = schema_def.get("properties", {}) if isinstance(schema_def, dict) else {}
                prop_names = ", ".join(props.keys()) if props else "(see contract)"
                lines.append(f"  {schema_name}: {{{prop_names}}}")

        return "\n".join(lines)

    # Multi-tier file classification for dependency ordering.
    # Lower tier number = generated earlier.  Files in tier N depend on
    # all same-domain files in tiers < N.
    _FILE_TIERS: Dict[int, List[str]] = {
        0: ["config", "settings", "env", "properties", "application", "db", "database"],
        10: ["model", "models", "entity", "entities", "schema", "schemas", "migration"],
        20: ["repository", "repositories", "dao", "store", "manager"],
        30: ["service", "services", "usecase", "use_case", "business", "logic"],
        40: ["serializer", "serializers", "dto", "mapper", "converter"],
        50: ["controller", "controllers", "handler", "handlers", "view", "views",
             "resource", "resources", "endpoint", "api"],
        60: ["middleware", "middlewares", "interceptor", "filter", "guard", "auth"],
        70: ["route", "routes", "router", "urls", "urlconf"],
        80: ["server", "app", "main", "index", "application", "wsgi", "asgi",
             "startup", "bootstrap"],
    }

    @staticmethod
    def _classify_file_tier(file_path: str, *, tdd: bool = False) -> int:
        """Assign a generation-order tier to a file based on its path components."""
        fp_lower = file_path.lower()
        if is_test_file_path(fp_lower):
            if tdd:
                return TaskManager._TEST_FILE_TIER_TDD
            return TaskManager._TEST_FILE_TIER
        stem = Path(file_path).stem.lower()
        parent = Path(file_path).parent.name.lower() if "/" in file_path else ""

        for tier, keywords in TaskManager._FILE_TIERS.items():
            for kw in keywords:
                if kw == stem or kw == parent or kw in fp_lower.split("/"):
                    return tier
        if "mock" in fp_lower:
            return 90
        return 50  # default: controller-level

    def _load_workspace_feature_files(self) -> Dict[str, str]:
        """Load all Gherkin feature files from the workspace features/ directory."""
        workspace_path = self.db_path.parent
        features_dir = workspace_path / "features"
        if not features_dir.is_dir():
            return {}
        contents: Dict[str, str] = {}
        for path in sorted(features_dir.glob("*.feature")):
            try:
                rel = str(path.relative_to(workspace_path)).replace("\\", "/")
                contents[rel] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        return contents

    def _match_feature_files(
        self, file_path: str, features_content: Dict[str, str],
    ) -> List[str]:
        """Return feature file paths whose titles/scenarios match *file_path* keywords."""
        if not features_content or not file_path:
            return []

        stop_words = frozenset({
            "src", "main", "java", "com", "org", "net", "test", "tests", "spec",
            "lib", "app", "api", "www", "bin", "obj", "out", "tmp",
        })
        keywords: set[str] = set()
        for segment in file_path.lower().replace("\\", "/").split("/"):
            for word in re.split(r"[_\-.]", segment):
                if len(word) >= 3 and word not in stop_words:
                    keywords.add(word)

        matched: List[str] = []
        for feat_path, content in features_content.items():
            feat_stem = Path(feat_path).stem.lower()
            title_match = re.search(r"Feature:\s*(.+)", content, re.IGNORECASE)
            feature_title = (
                title_match.group(1).strip().lower() if title_match else feat_stem
            )
            content_lower = content.lower()

            hit = False
            for kw in keywords:
                if kw in feature_title or kw in feat_stem:
                    hit = True
                    break
                if len(kw) >= 4 and kw in content_lower:
                    hit = True
                    break
            if hit:
                matched.append(feat_path)
        return matched

    def _infer_dependencies(
        self, task: TaskDefinition, earlier_tasks: List[TaskDefinition]
    ) -> List[str]:
        """Infer which earlier-tier tasks this task depends on.

        Uses multi-tier classification so that, e.g., controllers depend on
        services which depend on models which depend on config.  Within the
        same domain, a higher-tier file depends on all lower-tier files whose
        name stem appears in the file path.

        Test files depend only on their companion source module (resolved via
        language-agnostic naming conventions), not on every file whose stem
        appears in the path.
        """
        fp = (task.metadata or {}).get("file_path", "")
        fp_lower = fp.lower()
        tdd = getattr(self, "_tdd_mode", False)
        task_tier = self._classify_file_tier(fp_lower, tdd=tdd)

        if is_test_file_path(fp_lower):
            registered = [
                (et.metadata or {}).get("file_path", "")
                for et in earlier_tasks
                if (et.metadata or {}).get("file_path")
            ]
            companion = resolve_companion_source(fp, registered)
            if companion:
                for et in earlier_tasks:
                    if (et.metadata or {}).get("file_path", "") == companion:
                        return [et.task_id]
            return []

        deps: List[str] = []
        for et in earlier_tasks:
            et_fp = (et.metadata or {}).get("file_path", "").lower()
            et_tier = self._classify_file_tier(et_fp, tdd=tdd)
            if et_tier >= task_tier:
                continue
            et_stem = Path(et_fp).stem.replace("_", "")
            fp_flat = fp_lower.replace("_", "")
            if et_stem in fp_flat or et_stem.rstrip("s") in fp_flat:
                deps.append(et.task_id)

        return deps

    def _dependency_met_for_claim(
        self,
        dependent_fp: str,
        dep_task_id: str,
        dep_status: str,
        workspace: Path,
    ) -> bool:
        """Return True if *dep_task_id* is satisfied for claiming *dependent_fp*."""
        if dep_status in (TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value):
            return True
        if dep_status != TaskStatus.FAILED.value:
            return False
        # Unblock tests when the companion source exists even if that task failed
        if not is_test_file_path(dependent_fp.lower()):
            return False
        return companion_source_exists_on_disk(dependent_fp, workspace)

    # ── Per-task execution helpers ───────────────────────────────────────────

    def get_next_actionable_task(
        self, phase: str, *, task_id_filter: Optional[set] = None,
    ) -> Optional[TaskDefinition]:
        """Return the next registered task whose dependencies are all completed/skipped.

        If *task_id_filter* is given, only tasks whose ID is in the set are
        considered.  This allows parallel workers to process disjoint subsets.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT task_id, phase, task_type, description, required, source, status, metadata
            FROM tasks
            WHERE project_id = ? AND phase = ? AND status = ?
            ORDER BY created_at ASC
        """, (self.project_id, phase, TaskStatus.REGISTERED.value))

        for row in cursor.fetchall():
            task_id = row["task_id"]

            if task_id_filter is not None and task_id not in task_id_filter:
                continue

            # Check dependencies
            dep_rows = conn.execute(
                "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
                (task_id,),
            ).fetchall()

            all_deps_met = True
            for dr in dep_rows:
                dep_status = conn.execute(
                    "SELECT status FROM tasks WHERE task_id = ?",
                    (dr["depends_on_task_id"],),
                ).fetchone()
                if dep_status is None or dep_status["status"] not in (
                    TaskStatus.COMPLETED.value, TaskStatus.SKIPPED.value
                ):
                    all_deps_met = False
                    break

            if all_deps_met:
                conn.close()
                return TaskDefinition(
                    task_id=row["task_id"],
                    phase=row["phase"],
                    task_type=row["task_type"],
                    description=row["description"] or "",
                    required=bool(row["required"]),
                    source=row["source"],
                    status=row["status"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else None,
                    dependencies=[dr["depends_on_task_id"] for dr in dep_rows] if dep_rows else [],
                )

        conn.close()
        return None

    def get_and_claim_actionable_task(
        self, phase: str, *, task_id_filter: Optional[set] = None,
    ) -> Optional["TaskDefinition"]:
        """Atomically find and claim the next actionable task in a single transaction.

        Uses ``BEGIN IMMEDIATE`` so only one worker can claim a task at a time,
        preventing double-dispatch when multiple worker threads call this method
        concurrently.  Returns None when no task is ready (either all remaining
        tasks have unmet dependencies, or the queue is empty).
        """
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        workspace = self.db_path.parent
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("""
                SELECT task_id, phase, task_type, description, required, source, status, metadata
                FROM tasks
                WHERE project_id = ? AND phase = ? AND status = ?
                ORDER BY created_at ASC
            """, (self.project_id, phase, TaskStatus.REGISTERED.value))

            for row in cursor.fetchall():
                task_id = row["task_id"]
                if task_id_filter is not None and task_id not in task_id_filter:
                    continue

                dep_rows = conn.execute(
                    "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
                    (task_id,),
                ).fetchall()

                dependent_fp = ""
                if row["metadata"]:
                    try:
                        dependent_fp = json.loads(row["metadata"]).get("file_path", "") or ""
                    except Exception:
                        pass

                all_deps_met = all(
                    self._dependency_met_for_claim(
                        dependent_fp,
                        dr["depends_on_task_id"],
                        conn.execute(
                            "SELECT status FROM tasks WHERE task_id = ?",
                            (dr["depends_on_task_id"],),
                        ).fetchone()["status"],
                        workspace,
                    )
                    for dr in dep_rows
                ) if dep_rows else True

                if not all_deps_met:
                    continue

                # Atomically claim the task
                conn.execute("""
                    UPDATE tasks
                    SET status = ?, started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (TaskStatus.IN_PROGRESS.value, task_id))
                conn.commit()

                return TaskDefinition(
                    task_id=row["task_id"],
                    phase=row["phase"],
                    task_type=row["task_type"],
                    description=row["description"] or "",
                    required=bool(row["required"]),
                    source=row["source"],
                    status=TaskStatus.IN_PROGRESS.value,
                    metadata=json.loads(row["metadata"]) if row["metadata"] else None,
                    dependencies=[dr["depends_on_task_id"] for dr in dep_rows] if dep_rows else [],
                )

            conn.rollback()
            return None
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def has_pending_or_active_tasks(
        self, phase: str, task_id_filter: Optional[set] = None,
    ) -> bool:
        """Return True if any tasks in *task_id_filter* are REGISTERED or IN_PROGRESS.

        Used by parallel workers to decide whether to wait for new tasks to
        become actionable (a running task might complete and unblock its
        dependants) or to exit.
        """
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            cursor = conn.execute("""
                SELECT COUNT(*) FROM tasks
                WHERE project_id = ? AND phase = ?
                AND status IN (?, ?)
            """, (self.project_id, phase,
                  TaskStatus.REGISTERED.value, TaskStatus.IN_PROGRESS.value))
            total = cursor.fetchone()[0]
            if total == 0:
                return False
            if task_id_filter is None:
                return True
            # Check whether any of the active tasks are in our filter
            placeholders = ",".join("?" * len(task_id_filter))
            cursor = conn.execute(f"""
                SELECT COUNT(*) FROM tasks
                WHERE project_id = ? AND phase = ?
                AND status IN (?, ?)
                AND task_id IN ({placeholders})
            """, (self.project_id, phase,
                  TaskStatus.REGISTERED.value, TaskStatus.IN_PROGRESS.value,
                  *task_id_filter))
            return cursor.fetchone()[0] > 0
        finally:
            conn.close()

    def get_related_existing_files(
        self,
        task: TaskDefinition,
        completed_files: Dict[str, str],
        *,
        max_chars_per_file: int = 8192,
    ) -> Dict[str, str]:
        """Return dependency-related files only (not the entire completed set)."""
        related_paths: set[str] = set()
        for dep_id in task.dependencies or []:
            dep = self.get_task_by_id(dep_id)
            if dep and dep.metadata:
                fp = (dep.metadata or {}).get("file_path", "")
                if fp:
                    related_paths.add(fp)

        current_fp = (task.metadata or {}).get("file_path", "")
        if current_fp:
            current_dir = str(Path(current_fp).parent)
            if current_dir and current_dir != ".":
                for fp in completed_files:
                    if str(Path(fp).parent) == current_dir:
                        related_paths.add(fp)

        if not related_paths:
            return {}

        out: Dict[str, str] = {}
        for fp in sorted(related_paths):
            if fp not in completed_files:
                continue
            content = completed_files[fp]
            if len(content) > max_chars_per_file:
                content = content[:max_chars_per_file] + "\n# ... truncated ..."
            out[fp] = content
        return out

    def build_file_prompt(
        self,
        task: TaskDefinition,
        tech_stack: str = "",
        user_stories: str = "",
        existing_files: Optional[Dict[str, str]] = None,
        project_vision: str = "",
        max_project_vision_chars: Optional[int] = None,
        interface_contract: Optional[Dict[str, Any]] = None,
        api_contract: Optional[Dict[str, Any]] = None,
        rag_context: Optional[str] = None,
        context_window: Optional[int] = None,
        max_tokens: Optional[int] = None,
        simple_mode: bool = False,
        tldr_context: Optional[str] = None,
    ) -> str:
        """Build a focused prompt for generating a single file.

        The prompt includes project vision, domain context, file description,
        full content of related existing files, an interface contract of
        module exports, an OpenAPI contract for route/client files,
        cross-file consistency rules, and the tech stack so the LLM produces
        a complete, contextual implementation.
        """
        cap = max_project_vision_chars if max_project_vision_chars is not None else 14_000
        if project_vision and len(project_vision) > cap:
            before = len(project_vision)
            project_vision = project_vision[:cap] + "\n\n[... project vision truncated to fit API limit ...]"
            logger.warning(
                "Project vision truncated from %d to %d chars (max_project_vision_chars=%d)",
                before, len(project_vision), cap,
            )

        meta = task.metadata or {}
        file_path = (meta.get("file_path") or "").strip()
        # Guard: treat "unknown" as absent — it is never a real target filename.
        if file_path.lower() == "unknown":
            file_path = ""
        domain_ctx = meta.get("domain_context", "")
        file_desc = meta.get("file_description", "")

        # For BDD feature tasks (no target file_path), emit a behaviour-driven
        # implementation directive instead of "Create the file `unknown`...".
        is_feature_task = not file_path

        parts = []

        if project_vision:
            parts.append(f"PROJECT VISION: {project_vision}")
            parts.append("")

        if rag_context and rag_context.strip():
            parts.append("REFERENCE & PLAN EXCERPTS (retrieved — follow precisely):")
            parts.append(rag_context.strip())
            parts.append("")

        if tldr_context and tldr_context.strip():
            parts.append("CODEBASE STRUCTURE (from tldr — use for imports, call sites, and naming):")
            parts.append(tldr_context.strip())
            parts.append("")

        if is_feature_task:
            feature_name = task.description or meta.get("name", "the feature")
            scenarios = meta.get("scenarios", [])
            parts.append(
                f"Implement the feature: **{feature_name}**\n"
                "Write ALL production-quality files required to make this feature work.\n\n"
                "IMPORTANT INSTRUCTION:\n"
                "Refer to the PROJECT FILE TREE and tech stack below for structural guidance. "
                "Only create the layers, directories, or modules that are defined in the tech stack or are standard "
                "conventions for the framework. Do NOT unconditionally create models, services, or controllers "
                "unless they are explicitly expected by this project."
            )
            if existing_files:
                parts.append("\nExisting files in workspace:")
                for fp in existing_files.keys():
                    parts.append(f"  - {fp}")
            parts.append("")
            if scenarios:
                parts.append("Acceptance scenarios (BDD):")
                for s in scenarios:
                    parts.append(f"  - {s}")
                parts.append("")
        else:
            parts.append(
                f"Create the file `{file_path}` with a COMPLETE, production-quality implementation."
            )
            parts.append("")

        if file_desc:
            parts.append(f"File purpose: {file_desc}")
            parts.append("")

        if domain_ctx:
            parts.append(f"Domain context: {domain_ctx}")
            parts.append("")

        if tech_stack:
            parts.append(f"Technology stack:\n{tech_stack}")
            parts.append("")

        feature_file_paths = meta.get("feature_files") or []
        if feature_file_paths:
            workspace_path = self.db_path.parent
            parts.append("ACCEPTANCE CRITERIA (from Gherkin feature files):")
            for ff in feature_file_paths:
                feat_path = workspace_path / ff
                if not feat_path.is_file():
                    continue
                try:
                    feat_content = feat_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                parts.append(f"--- {ff} ---")
                parts.append(feat_content)
            parts.append("")
        elif user_stories:
            parts.append(f"User stories:\n{user_stories}")
            parts.append("")

        if interface_contract:
            parts.append("INTERFACE CONTRACT (agreed exports for each module):")
            for mod_path, exports in interface_contract.items():
                if isinstance(exports, dict):
                    named = exports.get("named", exports.get("exports", []))
                    has_default = exports.get("default", False)
                    export_str = ", ".join(named) if isinstance(named, list) else str(named)
                    if has_default:
                        export_str = f"default export + {export_str}" if export_str else "default export"
                elif isinstance(exports, list):
                    export_str = ", ".join(exports)
                else:
                    export_str = str(exports)
                parts.append(f"  {mod_path}: exports [{export_str}]")
            parts.append("")

        if api_contract and isinstance(api_contract, dict):
            contract_section = self._format_api_contract_for_file(file_path, api_contract)
            if contract_section:
                parts.append(contract_section)
                parts.append("")

        if existing_files:
            parts.append("Related files already created (use for imports/references):")
            trimmed_files = _trim_existing_files(
                existing_files,
                context_window=context_window,
                max_tokens=max_tokens,
                static_overhead_chars=sum(len(p) for p in parts),
            )
            for fp, content in trimmed_files.items():
                parts.append(f"--- {fp} ---")
                parts.append(content)
            parts.append("")
            parts.extend([
                "CROSS-FILE CONSISTENCY:",
                "- Only import modules/symbols that exist in the interface contract or the files listed above.",
                "- If you reference a class from another file, match its exact name and attributes.",
                "- Every local import must resolve to a real file in the project structure.",
                "- Every imported symbol must actually be exported by the target module.",
                "",
            ])

        wiring_hints = self._get_entrypoint_hints(file_path, tech_stack)
        if wiring_hints:
            parts.append("ENTRYPOINT WIRING (this file is the application entry point):")
            for hint in wiring_hints:
                parts.append(f"- {hint}")
            parts.append("")

        file_ext = Path(file_path).suffix.lower()
        if file_ext in self._JS_TS_EXTENSIONS:
            mod_sys = self._detect_module_system(tech_stack)
            if mod_sys and mod_sys in self._MODULE_SYSTEM_INSTRUCTIONS:
                parts.extend(self._MODULE_SYSTEM_INSTRUCTIONS[mod_sys])
                parts.append("")

        if is_test_file_path(file_path):
            fw = self._detect_test_framework(tech_stack)
            if fw and fw in self._TEST_FRAMEWORK_RULES:
                parts.extend(self._TEST_FRAMEWORK_RULES[fw])
                parts.append("")

        all_paths = sorted(self.get_registered_file_paths())
        if all_paths:
            parts.append("PROJECT FILE TREE (use these exact paths for imports):")
            for p in all_paths:
                parts.append(f"  {p}")
            parts.append("")

        import_hints = self._compute_import_hints(file_path, all_paths)
        if import_hints:
            parts.append("RESOLVED IMPORT PATHS (use these exact paths when importing project files):")
            for hint in import_hints:
                parts.append(f"  {hint}")
            parts.append("")

        req = [
            "REQUIREMENTS:",
            "- Write COMPLETE implementation code, not stubs or placeholders.",
            "- Include all necessary imports.",
            "- Implement ALL methods with real logic (no `pass`, no TODO, no console.log stubs).",
            "- Follow the patterns and conventions of the tech stack.",
        ]
        if simple_mode:
            if file_path:
                req += [
                    f"- Output ONLY a JSON array with one object for `{file_path}`.",
                    f'- Keys: "file_path" (exactly `{file_path}`) and "content" (full file body).',
                    "- Escape newlines inside content as \\n. Do NOT truncate large files.",
                    f"- Do NOT create any file other than `{file_path}`.",
                ]
            else:
                req += [
                    "- Output a JSON array: [{\"file_path\": \"...\", \"content\": \"...\"}, ...]",
                    "- Include every required source file with complete, untruncated content.",
                ]
        elif file_path:
            req += [
                f"- You MUST call file_writer(file_path='{file_path}', content='...') to create the file.",
                "- Do NOT just show code in your response; you must use the file_writer tool.",
                f"- ONLY create the file `{file_path}`. Do NOT output or create any other files.",
                "- Do NOT include test code or other file content in your response text.",
                "  Test files will be created in a separate task.",
            ]
        else:
            req += [
                "- Call file_writer(file_path='<path>', content='...') for EACH file you create.",
                "- Do NOT just show code in your response; you must use the file_writer tool.",
                "- Create ALL source files AND their corresponding test files.",
            ]
        req.append("")
        parts.extend(req)
        parts.extend([
            "CRITICAL — DO NOT HALLUCINATE APIs:",
            "- Only use APIs that actually exist in the libraries you import.",
            "- Do NOT invent methods, props, or classes. If unsure, use the simplest documented API.",
            "- Do NOT mix frameworks. If tech stack says Express, do NOT use NestJS decorators",
            "  (@Injectable, @InjectRepository, @Module). If tech stack says React, do NOT import Angular.",
            "- Express: create the app with `const app = express()`, NOT `express.createServer()`.",
            "- React: `import React from 'react'` (default export), NOT `import { React } from 'react'`.",
            "- Redux Toolkit: use `configureStore` from '@reduxjs/toolkit', NOT `createStore`.",
            "  `useSelector`/`useDispatch` come from 'react-redux', NOT '@reduxjs/toolkit'.",
            "- react-leaflet: `MapContainer`, `TileLayer` come from 'react-leaflet', NOT 'leaflet'.",
            "- TypeORM: import decorators (`Entity`, `Column`, `PrimaryGeneratedColumn`) directly",
            "  from 'typeorm', NOT via `declare module`.",
            "- Every npm/pip import MUST correspond to a real, published package.",
            "  Do NOT import packages from other ecosystems (e.g., no `flask` in a Node.js project).",
            "",
        ])
        if file_path:
            parts.append(f"Create `{file_path}` now.")
        else:
            feature_name = task.description or "the feature"
            parts.append(f"Implement `{feature_name}` now. Create every required file.")

        return "\n".join(parts)
