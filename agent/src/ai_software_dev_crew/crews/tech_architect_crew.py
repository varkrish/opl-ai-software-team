"""
Tech Architect Crew - Defines technology stack
"""
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from ..utils import get_llm_for_agent
from ..utils.prompt_loader import load_prompt
from ..tools import FileWriterTool, FileReaderTool


@CrewBase
class TechArchitectCrew:
    """Tech Architect Crew for defining technology stack"""

    def __init__(self):
        self._custom_backstories = {}

    @agent
    def tech_architect(self) -> Agent:
        default_backstory = load_prompt('tech_architect/tech_architect_backstory.txt', fallback="""You are a Technical Architect.
        Your goal is to translate logical designs into concrete technical decisions.
        You select specific technology stacks, enforce technical standards, and identify architectural risks.
        You consider the project vision and constraints when making decisions.""")
        
        backstory = self._custom_backstories.get('tech_architect', default_backstory)
        
        return Agent(
            role="Technical Architect",
            goal="Select tech stack and define technical standards",
            backstory=backstory,
            llm=get_llm_for_agent("manager"),
            verbose=True,
            allow_delegation=False,
            tools=[FileWriterTool(), FileReaderTool()]
        )

    @task
    def define_tech_stack(self) -> Task:
        task_desc = load_prompt('tech_architect/define_tech_stack_task.txt', fallback="""Review the design specification and define the concrete technology stack.
        
        Design Specification: {design_spec}
        Project Context: {context_digest}
        Project Vision: {vision}
        
        Select specific technologies (databases, frameworks, infrastructure) with justification.
        Save to tech_stack.md""")
        
        return Task(
            description=task_desc,
            agent=self.tech_architect(),
            expected_output="""Tech stack definition saved to tech_stack.md with:
            - Specific technology choices (database, backend framework, frontend framework, etc.)
            - Justification for each choice
            - Infrastructure requirements
            - Technical standards (API style, auth protocols, etc.)
            - Risk assessment"""
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Tech Architect Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

