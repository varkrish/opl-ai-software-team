"""
Task Manager with SQLite persistence
Manages task registry, creation tracking, and execution validation
"""
import sqlite3
import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Dict, Optional, Any
from pathlib import Path
from datetime import datetime
import re

logger = logging.getLogger(__name__)

VALID_FILE_EXTENSIONS = frozenset({
    'js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs',
    'py', 'pyw', 'pyi',
    'java', 'kt', 'scala', 'groovy', 'gradle',
    'go', 'rs', 'rb', 'php', 'swift', 'dart',
    'cpp', 'c', 'h', 'hpp', 'cc', 'cxx',
    'cs', 'vb', 'fs',
    'json', 'yaml', 'yml', 'toml', 'ini', 'cfg',
    'xml', 'xsd', 'xsl', 'wsdl', 'html', 'htm', 'css', 'scss', 'less', 'sass',
    'md', 'txt', 'rst', 'adoc',
    'sql', 'graphql', 'gql', 'proto',
    'sh', 'bash', 'zsh', 'bat', 'ps1', 'cmd',
    'config', 'conf', 'env', 'properties', 'lock',
    'gitignore', 'dockerignore', 'editorconfig',
    'dockerfile', 'containerfile',
    'podfile', 'xcworkspace',
    'vue', 'svelte', 'astro',
    'tf', 'hcl',
    'crt', 'pem', 'key', 'cer',
})


def _is_valid_file_path(name: str) -> bool:
    """Return True if *name* looks like a real file path, not a numbered list item or garbage."""
    if not name or len(name) < 2:
        return False
    # Reject purely numeric prefixes like "1.", "2.", "23."
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    if stem.isdigit():
        return False
    # Must have a recognised extension (the part after the last dot)
    if '.' not in name:
        return False
    ext = name.rsplit('.', 1)[1].lower()
    return ext in VALID_FILE_EXTENSIONS


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
                        expected_files.append(file_path)
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

    def reconcile_with_filesystem(self, workspace_path: Path):
        """Cross-check incomplete file creation tasks with the actual filesystem"""
        if not workspace_path.exists():
            return
            
        incomplete_tasks = self.get_incomplete_tasks()
        if not incomplete_tasks:
            return
            
        # Get all files in workspace for quick lookup
        all_files = {p.name: p for p in workspace_path.rglob("*") if p.is_file()}
        
        for task in incomplete_tasks:
            if task.task_type == "file_creation":
                file_path = (task.metadata or {}).get("file_path", "")
                if not file_path:
                    continue
                
                # Check exact path
                full_path = workspace_path / file_path
                if full_path.exists():
                    logger.info(f"🛡️ Self-healing: Found file {file_path} for task {task.task_id} via physical check")
                    self.update_task_status(task.task_id, "completed", f"File found on disk at {file_path}")
                    continue
                
                # Check basename fallback (if agent moved it)
                basename = Path(file_path).name
                if basename in all_files:
                    found_path = all_files[basename].relative_to(workspace_path)
                    logger.info(f"🛡️ Self-healing: Found file {basename} at {found_path} for task {task.task_id} via basename check")
                    self.update_task_status(task.task_id, "completed", f"File found on disk at {found_path}")
    
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

                # 2. Robust fallback: Search by metadata file_path or basename
                target_basename = Path(file_path).name
                for task in pending_tasks:
                    task_file_path = (task.metadata or {}).get("file_path", "")
                    if not task_file_path:
                        continue
                        
                    # Match by full path or just basename if it's a file creation task
                    if task_file_path == file_path or Path(task_file_path).name == target_basename:
                        logger.info(f"🎯 Found fallback completion marker for task: {task.task_id} (matched {file_path} to {task_file_path})")
                        self.update_task_status(task.task_id, "completed", f"File created: {file_path} (matched via {task_file_path})")
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
    ) -> List[TaskDefinition]:
        """Decompose design spec + tech stack into per-file tasks with domain context.

        Parses the file structure from tech_stack_content and cross-references
        bounded contexts from design_spec to attach domain context to each task.
        Model/core files are registered before views/controllers to express dependencies.
        File descriptions from tree comments are preserved in metadata.
        """
        file_entries = self._extract_files_with_descriptions(tech_stack_content)
        contexts = self._extract_bounded_contexts(design_spec)

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
                },
            )
            raw_tasks.append(task)

        # Auto-inject __init__.py for Python projects
        raw_tasks = self._inject_init_py_tasks(raw_tasks)

        # Sort tasks by tier so lower-tier files are registered first
        raw_tasks.sort(key=lambda t: self._classify_file_tier(
            (t.metadata or {}).get("file_path", "")
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
        return all_tasks

    def _inject_init_py_tasks(self, tasks: List[TaskDefinition]) -> List[TaskDefinition]:
        """Auto-inject ``__init__.py`` creation tasks for Python package directories.

        Scans registered file paths for ``.py`` files inside sub-directories and
        ensures every intermediate directory gets an ``__init__.py`` task.

        Skips a directory if a same-named flat module file already exists
        (e.g. won't create ``app/models/__init__.py`` when ``app/models.py``
        is already in the task list — that would be a file/package conflict).
        """
        py_files = [
            (t.metadata or {}).get("file_path", "")
            for t in tasks
            if (t.metadata or {}).get("file_path", "").endswith(".py")
        ]
        if not py_files:
            return tasks

        existing_paths = {(t.metadata or {}).get("file_path", "") for t in tasks}
        needed_inits: set = set()

        for fp in py_files:
            parts = Path(fp).parts
            for depth in range(1, len(parts)):
                dir_path = "/".join(parts[:depth])
                init_path = f"{dir_path}/__init__.py"
                if init_path in existing_paths:
                    continue
                flat_module = f"{dir_path}.py"
                if flat_module in existing_paths:
                    logger.info(
                        "Skipping %s — flat module %s already exists (file/package conflict)",
                        init_path, flat_module,
                    )
                    continue
                needed_inits.add(init_path)

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

        seen: set = set()
        deduped: List[Dict[str, str]] = []
        for e in entries:
            if e["path"] not in seen:
                seen.add(e["path"])
                deduped.append(e)
        return deduped

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

    # Multi-tier file classification for dependency ordering.
    # Lower tier number = generated earlier.  Files in tier N depend on
    # all same-domain files in tiers < N.
    _FILE_TIERS: Dict[int, List[str]] = {
        0: ["config", "settings", "env", "properties", "application", "db", "database"],
        1: ["model", "models", "entity", "entities", "schema", "schemas", "migration"],
        2: ["repository", "repositories", "dao", "store", "manager"],
        3: ["service", "services", "usecase", "use_case", "business", "logic"],
        4: ["serializer", "serializers", "dto", "mapper", "converter"],
        5: ["controller", "controllers", "handler", "handlers", "view", "views",
            "resource", "resources", "endpoint", "api"],
        6: ["middleware", "middlewares", "interceptor", "filter", "guard", "auth"],
        7: ["route", "routes", "router", "urls", "urlconf"],
        8: ["server", "app", "main", "index", "application", "wsgi", "asgi",
            "startup", "bootstrap"],
    }

    @staticmethod
    def _classify_file_tier(file_path: str) -> int:
        """Assign a generation-order tier to a file based on its path components."""
        fp_lower = file_path.lower()
        stem = Path(file_path).stem.lower()
        parent = Path(file_path).parent.name.lower() if "/" in file_path else ""

        for tier, keywords in TaskManager._FILE_TIERS.items():
            for kw in keywords:
                if kw == stem or kw == parent or kw in fp_lower.split("/"):
                    return tier
        # Tests and static assets go last
        if "test" in fp_lower or "spec" in fp_lower or "mock" in fp_lower:
            return 9
        return 5  # default: controller-level

    def _infer_dependencies(
        self, task: TaskDefinition, earlier_tasks: List[TaskDefinition]
    ) -> List[str]:
        """Infer which earlier-tier tasks this task depends on.

        Uses multi-tier classification so that, e.g., controllers depend on
        services which depend on models which depend on config.  Within the
        same domain, a higher-tier file depends on all lower-tier files whose
        name stem appears in the file path.
        """
        fp = (task.metadata or {}).get("file_path", "").lower()
        task_tier = self._classify_file_tier(fp)
        deps: List[str] = []

        for et in earlier_tasks:
            et_fp = (et.metadata or {}).get("file_path", "").lower()
            et_tier = self._classify_file_tier(et_fp)
            if et_tier >= task_tier:
                continue
            et_stem = Path(et_fp).stem.replace("_", "")
            fp_flat = fp.replace("_", "")
            if et_stem in fp_flat or et_stem.rstrip("s") in fp_flat:
                deps.append(et.task_id)

        return deps

    # ── Per-task execution helpers ───────────────────────────────────────────

    def get_next_actionable_task(self, phase: str) -> Optional[TaskDefinition]:
        """Return the next registered task whose dependencies are all completed/skipped."""
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

    def build_file_prompt(
        self,
        task: TaskDefinition,
        tech_stack: str = "",
        user_stories: str = "",
        existing_files: Optional[Dict[str, str]] = None,
        project_vision: str = "",
        max_project_vision_chars: Optional[int] = None,
        interface_contract: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build a focused prompt for generating a single file.

        The prompt includes project vision, domain context, file description,
        full content of related existing files, an interface contract of
        module exports, cross-file consistency rules, and the tech stack
        so the LLM produces a complete, contextual implementation.
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
        file_path = meta.get("file_path", "unknown")
        domain_ctx = meta.get("domain_context", "")
        file_desc = meta.get("file_description", "")

        parts = []

        if project_vision:
            parts.append(f"PROJECT VISION: {project_vision}")
            parts.append("")

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

        if user_stories:
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

        if existing_files:
            parts.append("Related files already created (use for imports/references):")
            for fp, content in existing_files.items():
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

        # Framework-specific wiring hints for entrypoint files
        wiring_hints = self._get_entrypoint_hints(file_path, tech_stack)
        if wiring_hints:
            parts.append("ENTRYPOINT WIRING (this file is the application entry point):")
            for hint in wiring_hints:
                parts.append(f"- {hint}")
            parts.append("")

        parts.extend([
            "REQUIREMENTS:",
            "- Write COMPLETE implementation code, not stubs or placeholders.",
            "- Include all necessary imports.",
            "- Implement ALL methods with real logic (no `pass`, no TODO, no console.log stubs).",
            "- Follow the patterns and conventions of the tech stack.",
            f"- You MUST call file_writer(file_path='{file_path}', content='...') to create the file.",
            "- Do NOT just show code in your response; you must use the file_writer tool.",
            "",
            f"Create `{file_path}` now.",
        ])

        return "\n".join(parts)
