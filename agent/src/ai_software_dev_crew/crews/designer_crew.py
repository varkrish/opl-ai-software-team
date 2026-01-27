"""
Designer Crew - Creates high-level design specifications
"""
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from ..utils import get_llm_for_agent
from ..utils.prompt_loader import load_prompt
from ..tools import FileWriterTool


@CrewBase
class DesignerCrew:
    """Designer Crew for creating logical architecture"""

    def __init__(self):
        self._custom_backstories = {}

    @agent
    def high_level_designer(self) -> Agent:
        default_backstory = load_prompt('designer/high_level_designer_backstory.txt', fallback="""You are a High-Level Design Agent.
        Your goal is to design logical architecture without committing to specific technologies.
        You use Domain-Driven Design (DDD), identify Bounded Contexts, define Data Flow and Domain Events.
        You create C4 Model diagrams and define component capabilities.""")
        
        backstory = self._custom_backstories.get('high_level_designer', default_backstory)
        
        return Agent(
            role="High-Level Designer",
            goal="Design logical architecture and system boundaries",
            backstory=backstory,
            llm=get_llm_for_agent("manager"),
            verbose=True,
            allow_delegation=False,
            tools=[FileWriterTool()]
        )

    @task
    def create_design_spec(self) -> Task:
        task_desc = load_prompt('designer/create_design_spec_task.txt', fallback="""Design the logical architecture for the user stories.
        
        User Stories: {user_stories}
        Project Context: {context_digest}
        
        Create design specification with bounded contexts, data flow, domain events, and component diagrams.
        Save to design_spec.md""")
        
        return Task(
            description=task_desc,
            agent=self.high_level_designer(),
            expected_output="""Design specification saved to design_spec.md with:
            - Bounded contexts identified
            - Data flow descriptions
            - Domain events defined
            - C4 Model diagrams (Context and Container levels)
            - Component capabilities
            - Interface contracts (abstract)"""
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Designer Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

