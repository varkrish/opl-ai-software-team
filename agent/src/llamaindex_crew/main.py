"""
Main entry point for LlamaIndex-based AI Software Development Crew
"""
import os
import sys
import logging
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file (lowest priority)
load_dotenv()

from .workflows.software_dev_workflow import SoftwareDevWorkflow
from .utils.llm_config import print_llm_config
from .config import ConfigLoader, SecretConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_workflow(
    vision: str,
    project_id: str = None,
    workspace_path: str = None,
    config: SecretConfig = None
):
    """
    Run the complete software development workflow
    
    Args:
        vision: Project vision/idea
        project_id: Optional project ID (defaults to timestamp-based)
        workspace_path: Optional workspace path (defaults to ./workspace)
        config: Optional SecretConfig instance (auto-loads if not provided)
    """
    import time
    
    # Load config if not provided
    if config is None:
        config = ConfigLoader.load()
    
    # Generate project ID if not provided
    if project_id is None:
        project_id = f"project_{int(time.time())}"
    
    # Set workspace path
    if workspace_path is None:
        workspace_path = Path(config.workspace.path) / project_id
    else:
        workspace_path = Path(workspace_path)
    
    # Create workspace directory
    workspace_path.mkdir(parents=True, exist_ok=True)
    
    # Set environment variable for tools
    os.environ["WORKSPACE_PATH"] = str(workspace_path)
    os.environ["PROJECT_ID"] = project_id
    
    logger.info(f"🚀 Starting workflow for project: {project_id}")
    logger.info(f"📁 Workspace: {workspace_path}")
    logger.info(f"💡 Vision: {vision[:100]}...")
    
    # Print LLM configuration
    print_llm_config(config)
    
    # Create and run workflow
    workflow = SoftwareDevWorkflow(
        project_id=project_id,
        workspace_path=workspace_path,
        vision=vision,
        config=config
    )
    
    try:
        results = workflow.run()
        
        logger.info("=" * 60)
        logger.info("✅ Workflow completed successfully!")
        logger.info("=" * 60)
        logger.info(f"Project ID: {results['project_id']}")
        logger.info(f"Status: {results['status']}")
        logger.info(f"State: {results['state']}")
        
        # Print budget report
        budget = results.get('budget_report', {})
        logger.info("\n💰 Budget Report:")
        logger.info(f"  Total Cost: ${budget.get('total_cost', 0):.4f}")
        logger.info(f"  Budget Limit: ${budget.get('budget_limit', 0):.2f}")
        logger.info(f"  Budget Used: {budget.get('budget_used_pct', 0):.1f}%")
        
        # Print task validation
        task_validation = results.get('task_validation', {})
        if task_validation.get('valid'):
            logger.info("\n✅ All tasks completed successfully")
        else:
            logger.warning("\n⚠️  Some tasks incomplete:")
            logger.warning(f"  Incomplete: {len(task_validation.get('incomplete_tasks', []))}")
            logger.warning(f"  Failed: {len(task_validation.get('failed_tasks', []))}")
        
        logger.info(f"\n📁 Output files in: {workspace_path}")
        
        return results
    except Exception as e:
        logger.error(f"❌ Workflow failed: {e}")
        raise


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="AI Software Development Crew (LlamaIndex) - Secure Configuration"
    )
    parser.add_argument(
        "vision",
        nargs="?",
        help="Project vision/idea"
    )
    parser.add_argument(
        "--project-id",
        help="Project ID (defaults to timestamp-based)"
    )
    parser.add_argument(
        "--workspace",
        help="Workspace path (defaults to config.workspace.path/{project_id})"
    )
    parser.add_argument(
        "--config",
        help="Path to secure configuration file (overrides auto-discovery)"
    )
    parser.add_argument(
        "--encryption-key",
        help="Encryption key for decrypting config secrets"
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print configuration and exit"
    )
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        config = ConfigLoader.load(
            config_path=args.config,
            encryption_key=args.encryption_key
        )
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)
    
    if args.show_config:
        print_llm_config(config)
        return
    
    if not args.vision:
        parser.error("Vision is required. Provide it as an argument or use --help for usage.")
    
    run_workflow(
        vision=args.vision,
        project_id=args.project_id,
        workspace_path=args.workspace,
        config=config
    )


if __name__ == "__main__":
    main()
