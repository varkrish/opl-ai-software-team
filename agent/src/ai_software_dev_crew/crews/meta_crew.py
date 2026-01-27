"""
Meta-Crew for dynamic agent configuration generation
This crew analyzes the project vision and generates tailored agent backstories
"""
import os
import json
import logging
from pathlib import Path
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from ..utils import get_llm_for_agent
from ..utils.prompt_loader import load_prompt as load_prompt_file
from ..tools import FileWriterTool

logger = logging.getLogger(__name__)


@CrewBase
class MetaCrew:
    """Meta-Crew that generates dynamic agent configurations based on project vision"""

    @agent
    def vision_agent(self) -> Agent:
        default_backstory = """You are the Vision Agent.
            Your goal is to analyze the raw project vision/idea and extract the core "Project DNA" to guide the team.
            Responsibilities:
            1. Identify the Target Audience and their specific pain points.
            2. Define the Product Tone (e.g., "Professional yet simple", "Playful", "Corporate").
            3. Extract Key Constraints (e.g., "Australian Law", "Mobile First").
            4. Summarize the Ultimate Value Prop.
            
            Output Format: A concise textual summary titled "Project Context Digest"."""
        
        backstory = load_prompt_file('vision_agent.txt', fallback=default_backstory)
        
        return Agent(
            role='Visionary',
            goal='Extract the core DNA and requirements from the project vision',
            backstory=backstory,
            llm=get_llm_for_agent("manager"),
            verbose=True,
            allow_delegation=False
        )

    @agent
    def prompter_agent(self) -> Agent:
        default_backstory = """You are the Prompter Agent.
            Your goal is to generate specific, tailored system prompts (backstories) for a CrewAI software development team 
            based on the "Project Context Digest". You must explicitly embed Open Practice Library (OPL) techniques into their personas.
            
            Responsibilities:
            1. Read the Project Context.
            2. Create a unique backstory for EACH role, ensuring they adopt specific OPL behaviors:
               - Product Owner: Must use Impact Mapping (Goal>Actor>Impact>Deliverable) and Mobius Loop thinking (Discovery -> Delivery).
               - High-Level Designer: Must use Event Storming (Domain Events) and Context Mapping (Bounded Contexts).
               - Tech Architect: Must use Architecture Decision Records (ADRs) and C4 Model terminology.
               - Coder: Must practice Horizontal Slicing (Database -> API -> Frontend). IMPORTANT: Do NOT hardcode the tech stack. 
                 The Coder's backstory must state: "You verify and use the technology stack defined by the Technical Architect's previous output."
               - Tester: Must use Exploratory Testing principles. IMPORTANT: Do NOT hardcode the tech stack. 
                 The Tester's backstory must state: "You derive your test strategy from the Technical Architect's stack and User Stories."
            
            CRITICAL OUTPUT FORMAT:
            You must output VALID JSON only. Do not wrap in markdown code blocks.
            Structure:
            {
              "business_analyst": "string",
              "developer": "string",
              "frontend_developer": "string",
              "code_reviewer": "string"
            }"""
        
        backstory = load_prompt_file('prompter_agent.txt', fallback=default_backstory)
        
        return Agent(
            role='Lead Prompter',
            goal='Generate bespoke persona backstories for the engineering crew based on the project vision',
            backstory=backstory,
            llm=get_llm_for_agent("manager"),
            verbose=True,
            allow_delegation=False,
            tools=[FileWriterTool()]
        )

    @task
    def analyze_vision(self) -> Task:
        return Task(
            description="""Analyze the following project vision/idea and produce a Project Context Digest:
            
            {vision}
            
            Extract:
            1. Target Audience and their specific pain points
            2. Product Tone (e.g., "Professional yet simple", "Playful", "Corporate")
            3. Key Constraints (e.g., legal requirements, platform requirements, technical constraints)
            4. Ultimate Value Proposition
            
            Output a concise textual summary titled "Project Context Digest".""",
            agent=self.vision_agent(),
            expected_output="A summary of the project vision, audience, and constraints."
        )

    @task
    def generate_prompts(self) -> Task:
        return Task(
            description="""Based on the Project Context Digest, generate the system prompts (backstories) for the 
            Business Analyst, Developer, Frontend Developer, and Code Reviewer. 
            
            IMPORTANT: 
            - Embed Open Practice Library (OPL) techniques into their personas
            - Business Analyst should use Impact Mapping and Mobius Loop thinking
            - Developer should practice Horizontal Slicing and use tech stack from architect
            - Frontend Developer should follow design system principles
            - Code Reviewer should use Exploratory Testing principles
            
            CRITICAL: You must output VALID JSON only. Do not wrap in markdown code blocks.
            Structure:
            {{
              "business_analyst": "detailed backstory for business analyst agent",
              "developer": "detailed backstory for developer agent",
              "frontend_developer": "detailed backstory for frontend developer agent",
              "code_reviewer": "detailed backstory for code reviewer agent"
            }}
            
            Also use the file_writer tool to save the generated prompts to 'agent_prompts.json' for reference.""",
            agent=self.prompter_agent(),
            expected_output="""JSON object with keys: business_analyst, developer, frontend_developer, code_reviewer.
            Each value should be a rich, motivating backstory that explicitly mentions OPL techniques and how they apply to THIS specific project.""",
            context=[self.analyze_vision()]
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Meta Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

