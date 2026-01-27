"""
Frontend Crew - UI/UX implementation
"""
import os
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from ..utils import get_llm_for_agent
from ..utils.prompt_loader import load_prompt
from ..utils.feature_parser import parse_features_from_files
from ..tools import (
    FileWriterTool, FileReaderTool, FileListTool,
    GitInitTool, GitCommitTool, GitStatusTool
)


def _is_git_enabled() -> bool:
    """Check if git operations are enabled"""
    git_enabled = os.getenv("ENABLE_GIT", "true").lower()
    return git_enabled in ("true", "1", "yes")


@CrewBase
class FrontendCrew:
    """Frontend Crew for UI/UX implementation"""
    
    def __init__(self):
        self._custom_backstories = {}

    @agent
    def frontend_developer(self) -> Agent:
        default_backstory = """You are a senior frontend developer with expertise in:
            - Modern web frameworks (React, Vue, Angular, Svelte)
            - HTML5, CSS3, JavaScript/TypeScript
            - Responsive design and mobile-first approach
            - Accessibility (WCAG guidelines)
            - UI/UX best practices
            - Component-based architecture
            - State management (Redux, Zustand, Context API)
            - CSS frameworks (Tailwind, Bootstrap, Material-UI)
            
            You create clean, maintainable, and well-documented frontend code."""
        
        backstory = self._custom_backstories.get('frontend_developer', default_backstory)
        
        return Agent(
            role="Senior Frontend Developer",
            goal="Build beautiful, responsive, and accessible user interfaces",
            backstory=backstory,
            llm=get_llm_for_agent("worker"),
            verbose=True,
            allow_delegation=False,
            tools=[
                FileWriterTool(),
                FileReaderTool(),
                FileListTool(),
                *([GitInitTool(), GitCommitTool(), GitStatusTool()] if _is_git_enabled() else [])
            ]
        )

    @agent
    def ui_reviewer(self) -> Agent:
        return Agent(
            role="UI/UX Quality Reviewer",
            goal="Review UI code for quality, accessibility, and best practices",
            backstory="""You are an experienced UI/UX reviewer who ensures:
            - Code follows accessibility standards (WCAG 2.1)
            - Responsive design works on all screen sizes
            - UI components are reusable and well-structured
            - Performance is optimized (lazy loading, code splitting)
            - User experience is intuitive and smooth
            - Cross-browser compatibility
            - Code follows modern frontend best practices""",
            llm=get_llm_for_agent("reviewer"),
            verbose=True,
            allow_delegation=False,
            tools=[
                FileReaderTool(),
                FileListTool(),
                FileWriterTool()
            ]
        )

    def _create_ui_feature_task(self, feature_name: str, feature_desc: str, feature_file: str = None) -> Task:
        """
        Create a task for implementing a single UI feature completely
        
        Args:
            feature_name: Name of the feature (e.g., "user-dashboard")
            feature_desc: Description of the feature
            feature_file: Path to feature file if available
            
        Returns:
            Task for implementing this UI feature
        """
        feature_context = ""
        if feature_file:
            feature_context = f"\n            Read the feature file: {feature_file} to understand the UI scenarios for this feature."
        else:
            feature_context = ""
        
        return Task(
            description=f"""Implement the '{feature_name}' UI feature completely.
            
            Feature: {feature_desc}
            Feature Name: {feature_name}
            {feature_context}
            
            Design Specifications: {{design_specs}}
            
            IMPORTANT: If design specifications are provided, follow them closely:
            - Use the UI/UX designs specified
            - Follow color schemes and styling guidelines
            - Implement responsive breakpoints as specified
            - Use the component library or framework specified
            
            IMPORTANT: Complete this ENTIRE UI feature before moving on. Do NOT start another feature.
            
            Follow this complete cycle for THIS UI feature only:
            
            1. **PLAN UI**: Analyze the feature requirements
               - Determine what UI components are needed
               - Plan the layout and structure
               - Identify user interactions needed
               - Check design specs for styling guidelines
            
            2. **CREATE HTML/STRUCTURE**: Build the markup
               - Create HTML files or component files
               - Structure the UI elements
               - Add semantic HTML for accessibility
               - Use appropriate HTML5 elements
            
            3. **STYLE WITH CSS**: Add styling
               - Create CSS files or use CSS-in-JS
               - Implement responsive design (mobile-first)
               - Add animations/transitions if needed
               - Ensure accessibility (contrast, focus states)
               - Follow design specs if provided
            
            4. **ADD INTERACTIVITY**: Implement JavaScript
               - Add event handlers
               - Implement user interactions
               - Handle form submissions if needed
               - Add client-side validation
               - Connect to backend APIs if needed
            
            5. **TEST & VERIFY**: 
               - Test on different screen sizes
               - Verify accessibility (keyboard navigation, screen readers)
               - Check cross-browser compatibility
               - Ensure all interactions work correctly
            
            6. **DOCUMENT**: Add documentation
               - Document component props/parameters
               - Add usage examples
               - Document any special considerations
            
            CRITICAL: Use file_writer to create files under src/:
            - src/App.js (or src/App.tsx) - main entry point
            - src/components/ - reusable components
            - src/__tests__/ or src/tests/ - test files
            
            You MUST call file_writer for each file and wait for "✅ Successfully wrote" confirmation.
            
            Choose appropriate technology stack based on tech_stack.md:
            - React Native: Create .js/.tsx files with RN components
            - React: Create .js/.jsx/.tsx files with React components
            - Plain web: Create HTML/CSS/JS files
            
            Do NOT implement other features. Only work on '{feature_name}'.""",
            agent=self.frontend_developer(),
            expected_output=f"""Complete UI implementation of '{feature_name}' feature with:

CRITICAL REQUIREMENTS:
1. ALL UI files MUST be created using file_writer tool under src/
2. You MUST see "✅ Successfully wrote" for EVERY file you create
3. Do NOT just describe files - actually call file_writer

Files created:
- src/App.js or src/components/<component>.js (implementation)
- src/__tests__/<component>.test.js (tests)"""
        )

    @task
    def review_ui(self) -> Task:
        return Task(
            description="""Review the implemented UI for quality, accessibility, and best practices.
            
            Perform the following checks:
            1. Accessibility (WCAG 2.1 compliance):
               - Keyboard navigation works
               - Screen reader compatibility
               - Color contrast ratios
               - Focus indicators
               - ARIA labels where needed
            
            2. Responsive Design:
               - Works on mobile (320px+)
               - Works on tablet (768px+)
               - Works on desktop (1024px+)
               - Layout adapts correctly
            
            3. Code Quality:
               - Clean, maintainable code
               - Reusable components
               - Proper separation of concerns
               - No inline styles (unless necessary)
            
            4. Performance:
               - Images optimized
               - CSS/JS minified (or ready for minification)
               - Lazy loading where appropriate
            
            5. User Experience:
               - Intuitive navigation
               - Clear feedback for user actions
               - Error handling
               - Loading states
            
            IMPORTANT: Use the file_writer tool to save your review to 'ui_review_report.md'
            
            If issues found, list them clearly with file paths and suggestions.""",
            agent=self.ui_reviewer(),
            expected_output="""UI review report saved to ui_review_report.md with:
            - Accessibility score (1-10)
            - Responsive design check results
            - Code quality assessment
            - Performance notes
            - List of issues (if any)
            - Approval status (approved/needs changes)"""
        )

    @crew
    def crew(self) -> Crew:
        """
        Creates the Frontend Crew with horizontal slicing
        Each UI feature is fully completed before moving to the next
        """
        # Parse features from feature files
        workspace_path = os.getenv("WORKSPACE_PATH", "./workspace")
        features = parse_features_from_files(workspace_path)
        
        # Filter for UI-related features or create UI tasks for all features
        # For now, we'll create UI tasks for all features (can be filtered later)
        ui_feature_tasks = []
        previous_task = None
        
        for feature in features:
            ui_task = self._create_ui_feature_task(
                feature['name'],
                feature['description'],
                feature.get('file')
            )
            
            # Set context to previous task if exists (sequential execution)
            if previous_task:
                ui_task.context = [previous_task]
            
            ui_feature_tasks.append(ui_task)
            previous_task = ui_task
        
        # If no features found, create a generic UI implementation task
        if not ui_feature_tasks:
            ui_feature_tasks = [self.implement_ui()]
        
        # Add review task at the end
        review_task = self.review_ui()
        if ui_feature_tasks:
            review_task.context = ui_feature_tasks
        
        # Combine all tasks
        all_tasks = ui_feature_tasks + [review_task]
        
        return Crew(
            agents=self.agents,
            tasks=all_tasks,
            process=Process.sequential,  # Sequential ensures one feature completes before next
            verbose=True
        )
    
    @task
    def implement_ui(self) -> Task:
        """Fallback task if no features are parsed"""
        # Load the prompt template
        prompt_template = load_prompt('frontend_crew/implement_ui_task.txt', fallback=None)
        
        if prompt_template:
            task_desc = prompt_template
        else:
            # Fallback to hardcoded description
            task_desc = """Implement UI from the requirements.
            
            Requirements: {requirements}
            Design Specifications: {design_specs}
            
            CRITICAL: You MUST use file_writer to create files under src/
            
            MANDATORY STEPS:
            1. Read tech_stack.md to understand the UI framework
            2. Call file_writer(file_path='src/App.js', content='<implementation>')
            3. Wait for "✅ Successfully wrote" confirmation
            4. Call file_writer for test file under src/tests/ or src/__tests__/
            
            Your final answer MUST list the files you created."""
        
        return Task(
            description=task_desc,
            agent=self.frontend_developer(),
            expected_output="""Complete UI implementation with files created under src/.

CRITICAL REQUIREMENTS:
1. ALL UI files MUST be created using file_writer tool
2. You MUST see "✅ Successfully wrote" for EVERY file you create
3. Do NOT just describe files - actually call file_writer
4. List the files you created with their paths in your response

Files created:
- src/App.js (or similar main component)
- src/__tests__/ or src/tests/ (test files)"""
        )

