"""
Refactor Runner
Orchestrates: Architect (analyze -> design -> plan) -> validate plan -> execute tasks.
Progress is reported via progress_callback AND job_db.update_progress when provided.
Returns a result dict with total_tasks, completed_tasks, and failed_tasks.

Refactored code is written to a *subdirectory* (``refactored/``) so that the
original workspace contents are never overwritten.
"""
import logging
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from agent.src.ai_software_dev_crew.refactor.agents.architect_agent import RefactorArchitectAgent
from agent.src.ai_software_dev_crew.refactor.agents.executor_agent import RefactorExecutorAgent

logger = logging.getLogger(__name__)

# Name of the subdirectories for isolation and output.
ORIGINAL_SOURCE_DIR = "original_source"
REFACTORED_OUTPUT_DIR = "refactored"

# DevOps agent (Containerfile + Tekton) — optional import so refactor works if agent not installed
try:
    from agent.src.llamaindex_crew.agents.devops_agent import DevOpsAgent
    DEVOPS_AGENT_AVAILABLE = True
except ImportError:
    DevOpsAgent = None
    DEVOPS_AGENT_AVAILABLE = False


def _validate_plan(tasks: List[Dict[str, Any]]) -> None:
    """Validate that every task in the plan has the required fields.

    Raises ValueError with a descriptive message on the first invalid task.
    """
    for task in tasks:
        task_id = task.get("id", "<unknown>")
        file_path = task.get("file")
        action = task.get("action", "modify")
        instruction = task.get("instruction")

        if not file_path:
            raise ValueError(
                f"Task {task_id}: 'file' is required and must be non-empty."
            )

        # instruction is required for modify and create; optional for delete
        if action in ("modify", "create") and not instruction:
            raise ValueError(
                f"Task {task_id}: 'instruction' is required for action '{action}'."
            )


def run_refactor_job(
    job_id: str,
    workspace_path: str,
    source_path: str,
    target_stack: str,
    tech_preferences: str = "",
    devops_instructions: str = "",
    job_db: Optional[object] = None,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Main entry point for running a refactor job.

    Returns:
        dict with keys: total_tasks, completed_tasks, failed_tasks
    """
    logger.info(f"Starting Refactor Job {job_id} on {workspace_path}")

    def update_progress(phase: str, percent: int, msg: str):
        if progress_callback:
            progress_callback(phase, percent, msg)
        if job_db:
            job_db.update_progress(job_id, phase, percent, msg)

    try:
        ws = Path(workspace_path)
        
        # 0. Isolate legacy source code into ORIGINAL_SOURCE_DIR
        # This keeps the root directory clean (only metadata and output subdirs).
        original_source_path = ws / ORIGINAL_SOURCE_DIR
        original_source_path.mkdir(exist_ok=True)
        
        # Move all files and folders that are NOT part of our system to original_source/
        # System files to skip: refactored/, original_source/, crew_jobs.db, etc.
        skip_items = {
            REFACTORED_OUTPUT_DIR, ORIGINAL_SOURCE_DIR, 
            "crew_jobs.db", ".git",
            "current_architecture.md", "target_architecture.md",
            "refactor_plan.json", "refactor_strategy.md"
        }
        
        moved_count = 0
        for item in ws.iterdir():
            if item.name in skip_items:
                continue
            
            target_item = original_source_path / item.name
            try:
                # If target already exists (e.g. from previous run), remove it
                if target_item.exists():
                    if target_item.is_dir():
                        shutil.rmtree(target_item)
                    else:
                        target_item.unlink()
                
                shutil.move(str(item), str(target_item))
                moved_count += 1
            except Exception as move_err:
                logger.warning(f"Failed to move legacy item {item.name} to {ORIGINAL_SOURCE_DIR}: {move_err}")
        
        if moved_count > 0:
            logger.info(f"Isolated {moved_count} items into {ORIGINAL_SOURCE_DIR}/")
            # Update source_path to point to the isolated original source
            source_path = str(original_source_path)

        # 1. Create refactored/ subdir — starts EMPTY (greenfield). No bulk copy.
        refactored_dir = ws / REFACTORED_OUTPUT_DIR
        if refactored_dir.exists():
            shutil.rmtree(refactored_dir)
        refactored_dir.mkdir(parents=True)
        logger.info(
            f"Refactored output dir {refactored_dir} created (greenfield — no legacy files copied)."
        )

        # 2. Initialize Agents: architect reads original workspace, executor writes to refactored/
        architect = RefactorArchitectAgent(workspace_path, job_id)
        executor = RefactorExecutorAgent(str(refactored_dir), job_id)

        # 3. Analyze Phase (architect reads from isolated source_path)
        update_progress("analysis", 10, "Analyzing source code structure...")
        architect.analyze(source_path)

        # 4. Check for architecture artifact (written by architect to original workspace)
        arch_path = ws / "current_architecture.md"
        if not arch_path.exists():
            logger.warning(
                "current_architecture.md was not created by the architect. "
                "The design phase will rely on conversation memory only."
            )

        # 5. Design Phase — produce target_architecture.md (future state) in original workspace
        update_progress("design", 20, f"Designing target architecture for {target_stack}...")
        architect.design(target_stack, tech_preferences)

        target_arch_path = ws / "target_architecture.md"
        if not target_arch_path.exists():
            logger.warning(
                "target_architecture.md was not created by the architect. "
                "The planning phase will rely on conversation memory only."
            )

        # 6. Planning Phase — produce refactor_plan.json in original workspace
        update_progress("planning", 35, f"Creating refactor plan for {target_stack}...")
        architect.plan(target_stack, tech_preferences)

        # 7. Read and validate the Plan (from original workspace)
        plan_path = ws / "refactor_plan.json"
        if not plan_path.exists():
            raise FileNotFoundError("Refactor plan was not created by the architect.")

        with open(plan_path, "r") as f:
            plan = json.load(f)

        tasks_from_plan = plan.get("tasks", [])
        _validate_plan(tasks_from_plan)

        # Populate DB tasks if database is available
        if job_db:
            job_db.delete_refactor_tasks(job_id)
            for i, t in enumerate(tasks_from_plan):
                db_task_id = f"refactor-{job_id[:8]}-{i}"
                job_db.create_refactor_task(
                    task_id=db_task_id,
                    job_id=job_id,
                    file_path=t.get("file", ""),
                    action=t.get("action", "modify"),
                    instruction=t.get("instruction", ""),
                )
            # Use DB tasks as the source of truth for execution
            tasks = job_db.get_refactor_tasks(job_id)
        else:
            # Fallback for runs without a database (e.g. CLI tests)
            tasks = tasks_from_plan

        total_tasks = len(tasks)

        # Copy architecture docs and plan into refactored/ for co-location with new code
        for doc in ["current_architecture.md", "target_architecture.md",
                    "refactor_plan.json", "refactor_strategy.md"]:
            src = ws / doc
            if src.exists():
                shutil.copy2(src, refactored_dir / doc)

        # 7. Execution Phase (executor writes only to refactored/)
        update_progress("execution", 50, f"Executing {total_tasks} refactor tasks...")

        completed_tasks = 0
        failed_tasks: List[Dict[str, str]] = []

        for i, task in enumerate(tasks):
            task_id = task.get("id", str(i))
            file_path = task.get("file_path" if job_db else "file", "")
            action = task.get("action", "modify")
            instruction = task.get("instruction", "")

            # Update DB status to running
            if job_db:
                job_db.update_refactor_task_status(task_id, "running")

            progress = 50 + int((i / max(total_tasks, 1)) * 40)
            update_progress("execution", progress, f"Refactoring {file_path} ({action})...")

            try:
                if action == "modify":
                    # Do NOT copy old code into refactored/. Pass original content to executor;
                    # executor writes only the refactored version.
                    # Original file is now isolated in ORIGINAL_SOURCE_DIR.
                    original_file = ws / ORIGINAL_SOURCE_DIR / file_path
                    source_content = None
                    if original_file.exists():
                        try:
                            source_content = original_file.read_text(errors="replace")
                        except Exception as read_err:
                            logger.warning(f"Could not read original file for modify: {file_path}: {read_err}")
                    else:
                        logger.warning(f"Original file not found for modify: {original_file}")
                    
                    executor.execute_task(
                        file_path, instruction, action="modify", source_content=source_content
                    )
                elif action == "create":
                    executor.execute_task(file_path, instruction, action="create")
                else:
                    # delete — greenfield: nothing to delete in refactored/
                    logger.info(
                        f"Skipping delete action for {file_path} "
                        "(greenfield — file not in refactored/)."
                    )

                if job_db:
                    job_db.update_refactor_task_status(task_id, "completed")
                completed_tasks += 1

            except Exception as task_err:
                logger.error(f"Task {task_id} ({file_path}) failed: {task_err}")
                if job_db:
                    job_db.update_refactor_task_status(task_id, "failed", error=str(task_err))
                failed_tasks.append({
                    "task_id": task_id,
                    "file": file_path,
                    "error": str(task_err),
                })

        # 7b. DevOps phase: add Containerfile and Tekton pipeline (modernized system must have them)
        devops_failed = False
        if DEVOPS_AGENT_AVAILABLE and DevOpsAgent is not None:
            update_progress("devops", 90, "Adding Containerfile and Tekton pipeline...")
            try:
                devops_agent = DevOpsAgent(refactored_dir, job_id)
                devops_agent.run(
                    tech_stack=target_stack,
                    pipeline_type="tekton",
                    project_context=devops_instructions.strip() or None,
                )
                logger.info(f"DevOps phase completed for job {job_id}")
            except Exception as devops_err:
                logger.error(f"DevOps phase failed for job {job_id}: {devops_err}")
                devops_failed = True
                failed_tasks.append({
                    "task_id": "devops",
                    "file": "Containerfile / .tekton",
                    "error": str(devops_err),
                })
        else:
            logger.debug("DevOps agent not available, skipping Containerfile/pipeline step")

        # 8. Completion
        if failed_tasks:
            update_progress(
                "completed", 95,
                f"Refactor finished with {len(failed_tasks)} failed task(s) "
                f"out of {total_tasks}.",
            )
        else:
            update_progress("completed", 100, "Refactor completed successfully.")

        result = {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
        }
        logger.info(f"Refactor Job {job_id} completed. Result: {result}")
        return result

    except Exception as e:
        logger.error(f"Refactor Job {job_id} failed: {e}")
        update_progress("failed", 0, f"Error: {str(e)}")
        raise
