"""
Meta Agent for dynamic agent configuration generation
Migrated from MetaCrew to LlamaIndex agent
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional
from .base_agent import BaseLlamaIndexAgent
from ..tools import FileWriterTool
from ..utils.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class MetaAgent:
    """Meta Agent that generates dynamic agent configurations based on project vision"""
    
    def __init__(self, budget_tracker=None):
        """
        Initialize Meta Agent
        
        Args:
            budget_tracker: Optional budget tracker instance
        """
        self.budget_tracker = budget_tracker
        
        # Create vision agent
        vision_backstory = load_prompt('vision_agent.txt', fallback="""You are the Vision Agent.
Your goal is to analyze the raw project vision/idea and extract the core "Project DNA" to guide the team.
Responsibilities:
1. Identify the Target Audience and their specific pain points.
2. Define the Product Tone (e.g., "Professional yet simple", "Playful", "Corporate").
3. Extract Key Constraints (e.g., "Australian Law", "Mobile First").
4. Summarize the Ultimate Value Prop.

Output Format: A concise textual summary titled "Project Context Digest".""")
        
        self.vision_agent = BaseLlamaIndexAgent(
            role='Visionary',
            goal='Extract the core DNA and requirements from the project vision',
            backstory=vision_backstory + "\n\nIMPORTANT: Do not use any tools. Just provide a textual response.",
            tools=[],  # Vision agent doesn't need tools
            agent_type="manager",
            budget_tracker=budget_tracker,
            verbose=True
        )
        
        # Create prompter agent
        prompter_backstory = load_prompt('prompter_agent.txt', fallback="""You are the Prompter Agent.
Your goal is to generate specific, tailored system prompts (backstories) for a software development team 
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
  "product_owner": "string",
  "designer": "string",
  "tech_architect": "string",
  "developer": "string",
  "frontend_developer": "string",
  "code_reviewer": "string"
}""")
        
        self.prompter_agent = BaseLlamaIndexAgent(
            role='Lead Prompter',
            goal='Generate bespoke persona backstories for the engineering crew based on the project vision',
            backstory=prompter_backstory + "\n\nIMPORTANT: Do not use any tools. Just output the JSON object directly as your Final Answer.",
            tools=[],  # Prompter agent doesn't need tools, it just needs to output JSON
            agent_type="manager",
            budget_tracker=budget_tracker,
            verbose=True
        )
    
    def analyze_vision(self, vision: str) -> str:
        """
        Analyze project vision and extract Project Context Digest
        
        Args:
            vision: Project vision/idea text
        
        Returns:
            Project Context Digest
        """
        prompt = f"""Analyze the following project vision/idea and produce a Project Context Digest:

{vision}

Extract:
1. Target Audience and their specific pain points
2. Product Tone (e.g., "Professional yet simple", "Playful", "Corporate")
3. Key Constraints (e.g., legal requirements, platform requirements, technical constraints)
4. Ultimate Value Proposition

Output a concise textual summary titled "Project Context Digest"."""
        
        response = self.vision_agent.chat(prompt)
        return str(response)
    
    def generate_agent_backstories(self, project_context: str) -> Dict[str, str]:
        """
        Generate agent backstories based on Project Context Digest
        
        Args:
            project_context: Project Context Digest from analyze_vision
        
        Returns:
            Dictionary of agent backstories
        """
        prompt = f"""Based on the Project Context Digest, generate SHORT system prompts (backstories) for the 
engineering crew. 

Project Context Digest:
{project_context}

IMPORTANT: 
- Be CONCISE. Keep each backstory under 200 words.
- Embed Open Practice Library (OPL) techniques.
- Product Owner: Impact Mapping, Mobius Loop.
- Designer: Event Storming, Context Mapping.
- Tech Architect: ADRs, C4 Model.
- Developer: Horizontal Slicing, TDD.
- Frontend: Design System.
- Code Reviewer: Exploratory Testing.

CRITICAL: Output ONLY valid JSON.
Structure:
{{
  "product_owner": "backstory...",
  "designer": "backstory...",
  "tech_architect": "backstory...",
  "developer": "backstory...",
  "frontend_developer": "backstory...",
  "code_reviewer": "backstory..."
}}"""
        
        response = self.prompter_agent.chat(prompt)
        response_str = str(response)
        
        # Parse JSON from response (may be wrapped in markdown code blocks)
        try:
            # Try to extract JSON from markdown code blocks
            if "```json" in response_str:
                json_start = response_str.find("```json") + 7
                json_end = response_str.find("```", json_start)
                json_str = response_str[json_start:json_end].strip()
            elif "```" in response_str:
                json_start = response_str.find("```") + 3
                json_end = response_str.find("```", json_start)
                json_str = response_str[json_start:json_end].strip()
            else:
                json_str = response_str.strip()
            
            # Parse JSON
            backstories = json.loads(json_str)
            
            # Save to file
            workspace_path = Path(os.getenv("WORKSPACE_PATH", "./workspace"))
            output_file = workspace_path / "agent_prompts.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(backstories, f, indent=2)
            
            logger.info(f"✅ Saved agent backstories to {output_file}")
            
            return backstories
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from response: {e}")
            logger.error(f"Response was: {response_str[:500]}")
            # Return default backstories if parsing fails
            return self._get_default_backstories()
    
    def _get_default_backstories(self) -> Dict[str, str]:
        """Get default backstories if JSON parsing fails"""
        return {
            "product_owner": "You are a Product Owner who uses Impact Mapping and Mobius Loop thinking.",
            "designer": "You are a Designer who uses Event Storming and Context Mapping.",
            "tech_architect": "You are a Tech Architect who uses Architecture Decision Records (ADRs) and C4 Model.",
            "developer": "You are a Developer who practices Horizontal Slicing and uses the tech stack defined by the Technical Architect.",
            "frontend_developer": "You are a Frontend Developer who follows design system principles.",
            "code_reviewer": "You are a Code Reviewer who uses Exploratory Testing principles."
        }
    
    def run(self, vision: str) -> Dict[str, str]:
        """
        Run the complete meta agent workflow
        
        Args:
            vision: Project vision/idea
        
        Returns:
            Dictionary of agent backstories
        """
        # Step 1: Analyze vision
        project_context = self.analyze_vision(vision)
        
        # Step 2: Generate backstories
        backstories = self.generate_agent_backstories(project_context)
        
        return backstories
