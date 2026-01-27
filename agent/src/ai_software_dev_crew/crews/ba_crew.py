"""
Business Analyst Crew - Requirements analysis and Gherkin scenario creation
"""
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from typing import List
from pydantic import BaseModel
from ..utils import get_llm_for_agent
from ..tools import FileWriterTool


class RequirementsOutput(BaseModel):
    """Structured output for requirements"""
    user_stories: List[str]
    gherkin_scenarios: str
    acceptance_criteria: List[str]
    technical_requirements: List[str]


@CrewBase
class BACrew:
    """Business Analyst Crew for requirements gathering and BDD scenario creation"""
    
    def __init__(self):
        self._custom_backstories = {}

    @agent
    def business_analyst(self) -> Agent:
        default_backstory = """You are a senior Business Analyst with 15 years of experience in software development.
            You excel at translating business needs into clear, testable requirements using
            Behavior-Driven Development (BDD) practices with Gherkin syntax. You ask clarifying questions
            when requirements are ambiguous and ensure all acceptance criteria are measurable."""
        
        backstory = self._custom_backstories.get('business_analyst', default_backstory)
        
        return Agent(
            role="Senior Business Analyst",
            goal="Analyze user vision and create detailed, testable requirements using BDD/Gherkin",
            backstory=backstory,
            llm=get_llm_for_agent("manager"),
            verbose=True,
            allow_delegation=False,
            tools=[FileWriterTool()]
        )

    @agent
    def requirements_validator(self) -> Agent:
        return Agent(
            role="Requirements Quality Assurance Specialist",
            goal="Validate requirements for completeness, clarity, and testability",
            backstory="""You are a meticulous requirements validator who ensures that all requirements
            are SMART (Specific, Measurable, Achievable, Relevant, Time-bound). You check for ambiguities,
            missing edge cases, and ensure Gherkin scenarios are properly structured.""",
            llm=get_llm_for_agent("reviewer"),
            verbose=True,
            allow_delegation=False,
            tools=[FileWriterTool()]
        )

    @task
    def analyze_vision(self) -> Task:
        return Task(
            description="""Analyze the user's vision and create comprehensive requirements.
            
            User Vision: {vision}
            
            Design Specifications: {design_specs}
            
            IMPORTANT: If design specifications are provided, carefully review them and incorporate:
            - Architecture decisions from design docs
            - API specifications from API docs
            - Database schemas from schema files
            - UI/UX requirements from design files
            - Technical constraints from technical specs
            - Any other relevant design information
            
            Your analysis should include:
            1. User stories in the format: "As a [role], I want [feature], so that [benefit]"
            2. Gherkin scenarios for each user story using Given-When-Then format
            3. Clear acceptance criteria
            4. Technical requirements and constraints
            5. Non-functional requirements (performance, security, scalability)
            
            CRITICAL - YOU MUST WRITE FILES TO DISK:
            
            1. **MANDATORY**: Use the file_writer tool to save your complete requirements analysis to 'requirements.md'
               - This is REQUIRED, not optional
               - The file must be written to the workspace root: 'requirements.md'
               - Include ALL your analysis: user stories, Gherkin scenarios, acceptance criteria, technical requirements
               - Example: file_writer(file_path='requirements.md', content='[your full requirements document]')
            
            2. **MANDATORY**: Extract all Gherkin scenarios and save them as separate .feature files in the 'features/' directory
               - Each user story should have its own feature file (e.g., 'features/addition.feature', 'features/subtraction.feature')
               - Feature files must follow proper Gherkin format:
                 
                 Example format:
                 Feature: Addition
                   As a user
                   I want to add two numbers
                   So that I can find their sum
                 
                   Scenario: Add two positive integers
                     Given the calculator is ready
                     When I enter "5" and "7"
                     And I choose the "add" operation
                     Then the result should be "12"
               
               - Use descriptive feature file names based on the functionality
               - Create one .feature file per user story/feature
               - Use file_writer tool for each feature file: file_writer(file_path='features/[name].feature', content='[gherkin content]')
            
            REMEMBER: You MUST use the file_writer tool. Do not just return text - write files to disk!
            
            Ask clarifying questions if the vision is ambiguous.""",
            agent=self.business_analyst(),
            expected_output="""A comprehensive requirements document saved to requirements.md AND separate .feature files in features/ directory with:
            - 3-5 user stories
            - Gherkin scenarios for each story (saved as .feature files)
            - Detailed acceptance criteria
            - Technical and non-functional requirements
            - Each feature file should be properly formatted Gherkin with Feature, Scenario, Given, When, Then"""
        )

    @task
    def validate_requirements(self) -> Task:
        return Task(
            description="""Review and validate the requirements document for quality.
            
            Check for:
            1. Completeness - Are all aspects covered?
            2. Clarity - Are requirements unambiguous?
            3. Testability - Can each requirement be tested?
            4. Gherkin syntax correctness in both requirements.md and .feature files
            5. Missing edge cases or error scenarios
            6. Feature files exist in features/ directory and are properly formatted
            
            IMPORTANT: Use the file_writer tool to save your validation report to 'validation_report.md'
            
            Provide specific feedback and improvements.""",
            agent=self.requirements_validator(),
            expected_output="""A validation report saved to validation_report.md with:
            - Quality score (1-10)
            - List of issues found
            - Recommended improvements
            - Final approved requirements document""",
            context=[self.analyze_vision()]
        )

    @crew
    def crew(self) -> Crew:
        """Creates the BA Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

