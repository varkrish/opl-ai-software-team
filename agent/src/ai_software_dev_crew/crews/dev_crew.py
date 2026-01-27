"""
Development Crew - TDD-based code implementation with horizontal slicing
Each feature is fully completed (test → implement → test → document) before moving to next
"""
import os
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from ..utils import get_llm_for_agent
from ..utils.prompt_loader import load_prompt
from ..utils.feature_parser import parse_features_from_files
from ..tools import (
    FileWriterTool, FileReaderTool, FileListTool,
    GitInitTool, GitCommitTool, GitStatusTool,
    PytestRunnerTool, CodeCoverageTool
)


def _is_git_enabled() -> bool:
    """Check if git operations are enabled"""
    git_enabled = os.getenv("ENABLE_GIT", "true").lower()
    return git_enabled in ("true", "1", "yes")


@CrewBase
class DevCrew:
    """Development Crew for TDD-based implementation"""
    
    def __init__(self):
        self._custom_backstories = {}

    @agent
    def backend_developer(self) -> Agent:
        default_backstory = load_prompt('dev_crew/developer_backstory.txt', fallback="""You are a Senior Full-Stack Developer.
        Your goal is to write clean, functional, and testable code based on the Technical Architect's specifications.
        You are strictly the implementer. You MUST use the technology stack defined in tech_stack.md.
        You do NOT choose your own stack - you use what the architect specified.
        You follow SOLID principles, DRY, and Clean Code practices.""")
        
        backstory = self._custom_backstories.get('developer', default_backstory)
        
        return Agent(
            role="Senior Full-Stack Developer (TDD Practitioner)",
            goal="Implement code following strict TDD: Red-Green-Refactor cycle. Choose the appropriate technology stack based on requirements.",
            backstory=backstory,
            llm=get_llm_for_agent("worker"),
            verbose=True,
            allow_delegation=False,
            tools=[
                FileWriterTool(),
                FileReaderTool(),
                FileListTool(),
                *([GitInitTool(), GitCommitTool(), GitStatusTool()] if _is_git_enabled() else []),
                PytestRunnerTool(),
                CodeCoverageTool()
            ]
        )

    @agent
    def code_reviewer(self) -> Agent:
        return Agent(
            role="Code Quality Reviewer",
            goal="Review code for quality, test coverage, and adherence to best practices",
            backstory="""You are an experienced code reviewer who ensures code quality standards.
            You check for:
            - Test coverage >= 80%
            - Clean code principles (SOLID, DRY, KISS)
            - Proper error handling
            - Security best practices
            - Documentation and comments
            - Performance considerations""",
            llm=get_llm_for_agent("reviewer"),
            verbose=True,
            allow_delegation=False,
            tools=[
                FileReaderTool(),
                FileListTool(),
                FileWriterTool(),
                PytestRunnerTool(),
                CodeCoverageTool()
            ]
        )

    def _create_feature_task(self, feature_name: str, feature_desc: str, feature_file: str = None) -> Task:
        """
        Create a task for implementing a single feature completely
        
        Args:
            feature_name: Name of the feature (e.g., "addition")
            feature_desc: Description of the feature
            feature_file: Path to feature file if available
            
        Returns:
            Task for implementing this feature
        """
        feature_context = ""
        if feature_file:
            feature_context = f"\n            Read the feature file: {feature_file} to understand the scenarios for this feature."
        
        # Read tech_stack.md if it exists
        import os
        from pathlib import Path
        workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
        tech_stack_file = workspace_path / "tech_stack.md"
        tech_stack_info = ""
        if tech_stack_file.exists():
            with open(tech_stack_file, 'r') as f:
                tech_stack_info = f"\n\nTECH STACK (from tech_stack.md - YOU MUST USE THIS):\n{f.read()}\n"
        
        # Load the prompt template
        prompt_template = load_prompt('dev_crew/implement_feature.txt', fallback=None)
        
        # If prompt loading fails (shouldn't happen if setup correctly), fall back to hardcoded
        if not prompt_template:
            # Reverting to the string defined in the file if loading fails would be complex here due to size
            # Ideally load_prompt rasies error or we have a solid fallback. 
            # For now, let's assume it works as we just created the file.
            pass

        return Task(
            description=prompt_template.format(
                feature_name=feature_name,
                feature_desc=feature_desc,
                feature_context=feature_context,
                design_specs="{design_specs}", # Leave as placeholder for runtime
                tech_stack_info=tech_stack_info
            ),
            agent=self.backend_developer(),
            expected_output=f"""Complete implementation of '{feature_name}' feature.

CRITICAL REQUIREMENTS:
1. ALL code files MUST be created using file_writer tool
2. You MUST see "✅ Successfully wrote" for EVERY file you create  
3. Do NOT just describe files - actually call file_writer
4. List the files you created with their paths in your response

Implementation must include:
            - Appropriate technology stack chosen based on requirements
            - All necessary files created (HTML/CSS/JS for web pages, or src/tests for backend)
            - Tests written and passing (if applicable)
            - Documentation added
            - Feature is 100% complete and functional"""
        )

    @task
    def review_code(self) -> Task:
        return Task(
            description="""Review the implemented code for quality and best practices.
            
            Perform the following checks:
            1. Run all tests and verify they pass
            2. Check test coverage (must be >= 80%)
            3. Review code for:
               - Clean code principles
               - Proper error handling
               - Security issues
               - Performance concerns
               - Documentation quality
            4. Provide specific feedback and required changes
            
            IMPORTANT: Use the file_writer tool to save your review to 'code_review_report.md'
            
            If issues found, list them clearly with line numbers and suggestions.""",
            agent=self.code_reviewer(),
            expected_output="""Code review report saved to code_review_report.md with:
            - Test results (pass/fail)
            - Coverage percentage
            - Code quality score (1-10)
            - List of issues (if any)
            - Approval status (approved/needs changes)"""
        )

    @crew
    def crew(self) -> Crew:
        """
        Creates the Development Crew with horizontal slicing
        Each feature is fully completed before moving to the next
        """
        # Parse features from feature files
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        features = parse_features_from_files(workspace_path)
        
        # Create tasks dynamically for each feature
        feature_tasks = []
        previous_task = None
        
        for feature in features:
            feature_task = self._create_feature_task(
                feature['name'],
                feature['description'],
                feature.get('file')
            )
            
            # Set context to previous task if exists (sequential execution)
            if previous_task:
                feature_task.context = [previous_task]
            
            feature_tasks.append(feature_task)
            previous_task = feature_task
        
        # If no features found, fall back to single implementation task
        if not feature_tasks:
            # Create a generic implementation task
            feature_tasks = [self.implement_feature()]
        
        # Add review task at the end (depends on all feature tasks)
        review_task = self.review_code()
        if feature_tasks:
            review_task.context = feature_tasks
        
        # Combine all tasks
        all_tasks = feature_tasks + [review_task]
        
        return Crew(
            agents=self.agents,
            tasks=all_tasks,
            process=Process.sequential,  # Sequential ensures one feature completes before next
            verbose=True
        )
    
    @task
    def implement_feature(self) -> Task:
        """Fallback task if no features are parsed"""
        # Read tech_stack.md if it exists
        import os
        from pathlib import Path
        workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
        tech_stack_file = workspace_path / "tech_stack.md"
        tech_stack_info = ""
        if tech_stack_file.exists():
            with open(tech_stack_file, 'r') as f:
                tech_stack_info = f"\n\nTECH STACK (from tech_stack.md - YOU MUST USE THIS):\n{f.read()}\n"
        
        # Load the prompt template if available
        prompt_template = load_prompt('dev_crew/implement_feature.txt', fallback=None)
        
        if prompt_template:
            # Use the template with proper formatting
            task_desc = prompt_template.format(
                feature_name="all features",
                feature_desc="Implement all features from requirements",
                feature_context="\n            Read requirements.md and features/ directory to understand what needs to be implemented.",
                design_specs="{design_specs}",
                tech_stack_info=tech_stack_info
            )
        else:
            # Fallback to hardcoded description
            task_desc = f"""Implement features from the requirements using TDD methodology.
            
            Requirements: {{requirements}}
            
            Design Specifications: {{design_specs}}
            {tech_stack_info}
            
            CRITICAL: You MUST use the technology stack defined in tech_stack.md. Do NOT choose your own stack.
            The tech stack has been defined by the Technical Architect - use it exactly as specified.
            
            IMPORTANT: If design specifications are provided, follow them closely:
            - Use the architecture patterns specified
            - Follow API contracts from design docs
            - Adhere to database schemas if provided
            - Implement according to technical specifications
            
            Read requirements.md and features/ directory to understand what needs to be implemented.
            Implement each feature completely (test → implement → test → document) before moving to the next.
            
            **CRITICAL - YOU MUST USE THE file_writer TOOL TO CREATE FILES:**
            
            You MUST use the file_writer tool to create ALL code files. Do NOT just describe code - actually create the files!
            
            **CHECK FILES FIRST**: Always check if files exist using file_reader before writing.
            **UPDATE, DON'T OVERWRITE**: If file exists, read it and APPEND your new code. Do not delete existing code.
            
            **IMPORTANT**: 
            - The file_path parameter must be a string (e.g., 'index.html', 'src/main.py')
            - The content parameter must be a string containing the complete file content
            - You MUST create actual files, not just describe what should be in them
            - Every file you mention MUST be created using file_writer"""
        
        return Task(
            description=task_desc,
            agent=self.backend_developer(),
            expected_output="""Complete implementation with all features. 

CRITICAL REQUIREMENTS:
1. ALL code files MUST be created using file_writer tool
2. You MUST see "✅ Successfully wrote" for EVERY file you create
3. Do NOT just describe files - actually call file_writer
4. List the files you created with their paths in your response"""
        )

