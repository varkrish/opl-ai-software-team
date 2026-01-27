"""
Product Owner Crew - Creates user stories from vision
"""
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from ..utils import get_llm_for_agent
from ..utils.prompt_loader import load_prompt
from ..tools import FileWriterTool


@CrewBase
class ProductOwnerCrew:
    """Product Owner Crew for creating user stories"""

    def __init__(self):
        self._custom_backstories = {}

    @agent
    def product_owner(self) -> Agent:
        default_backstory = load_prompt('product_owner/product_owner_backstory.txt', fallback="""You are a Product Owner.
        Your goal is to maximize value for stakeholders by defining clear requirements.
        You use Impact Mapping: Goal -> Actor -> Impact -> Deliverable.
        You break down requests into User Stories with Acceptance Criteria using Gherkin.""")
        
        backstory = self._custom_backstories.get('product_owner', default_backstory)
        
        return Agent(
            role="Product Owner",
            goal="Define user requirements and create user stories",
            backstory=backstory,
            llm=get_llm_for_agent("manager"),
            verbose=True,
            allow_delegation=False,
            tools=[FileWriterTool()]
        )

    @task
    def create_user_stories(self) -> Task:
        task_desc = load_prompt('product_owner/create_user_stories_task.txt', fallback="""Create User Stories based on the project vision.
        
        User Vision: {vision}
        Project Context Digest: {context_digest}
        
        Create user stories with acceptance criteria using Gherkin format.
        Save to user_stories.md and feature files.""")
        
        return Task(
            description=task_desc,
            agent=self.product_owner(),
            expected_output="""User stories saved to user_stories.md and .feature files in features/ directory with:
            - User stories in format: As a [role], I want [feature], so that [benefit]
            - Acceptance criteria in Gherkin format (Given... When... Then...)
            - Business value explanations"""
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Product Owner Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

