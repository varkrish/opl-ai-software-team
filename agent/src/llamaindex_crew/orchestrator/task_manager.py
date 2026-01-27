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
                        'README.md', 'Dockerfile', 'docker-compose.yml'
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
    
    def validate_all_tasks_completed(self) -> Dict[str, Any]:
        """Validate that all required tasks were completed"""
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
    
    def get_incomplete_tasks(self) -> List[TaskDefinition]:
        """Get list of tasks that were created but not completed"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT task_id, phase, task_type, description, required, source, metadata
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
                metadata=json.loads(row[6]) if row[6] else None
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
            SELECT task_id, phase, task_type, description, required, source, metadata
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
                metadata=json.loads(row[6]) if row[6] else None
            ))
        conn.close()
        return tasks

    def _get_tasks_by_status(self, status: TaskStatus) -> List[TaskDefinition]:
        """Get tasks by status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT task_id, phase, task_type, description, required, source, metadata
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
                metadata=json.loads(row[6]) if row[6] else None
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
        
        logger.debug(f"Scanning output for task updates: {output[:200]}...")
        
        # Look for file creation markers
        patterns = [
            r"Successfully wrote to ([a-zA-Z0-9_\-\.\/]+)",
            r"Created ([a-zA-Z0-9_\-\.\/]+)",
            r"✅ Created ([a-zA-Z0-9_\-\.\/]+)",
            r"✅ Successfully wrote to ([a-zA-Z0-9_\-\.\/]+)"
        ]
        
        found_any = False
        for pattern in patterns:
            matches = re.findall(pattern, output)
            for file_path in matches:
                # Clean up file path (remove trailing punctuation)
                file_path = file_path.strip().rstrip('.').rstrip(')')
                task_id = f"file_{self.normalize_file_path_for_task_id(file_path)}"
                logger.info(f"🎯 Found completion marker for task: {task_id} (file: {file_path})")
                self.update_task_status(task_id, "completed", f"File created: {file_path}")
                found_any = True
        
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
