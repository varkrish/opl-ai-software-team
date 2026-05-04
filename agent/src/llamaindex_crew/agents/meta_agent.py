"""
Meta Agent for dynamic agent configuration generation
Migrated from MetaCrew to LlamaIndex agent
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional
from ..tools import FileWriterTool, GitTool
from ..tools.tool_loader import load_tools
from ..config import ConfigLoader
from ..utils.prompt_loader import load_prompt
from .base_agent import BaseLlamaIndexAgent

logger = logging.getLogger(__name__)


class ImportModeRecommendedError(Exception):
    """Triage decided import+iterate fits better than the greenfield build pipeline."""

    def __init__(self, message: str, triage: Dict[str, str]):
        super().__init__(message)
        self.triage = triage


def user_vision_for_triage(full_vision: str) -> str:
    """Strip Repomix/reference packs — they are style guides, not 'existing product code' for routing."""
    marker = "\n\n=== REFERENCE"
    if marker in full_vision:
        return full_vision.split(marker, 1)[0].strip()
    return full_vision.strip()


def workspace_source_hint(workspace_path: Path) -> str:
    """Short hint when the job workspace already contains many source files (unusual for pure greenfield)."""
    if not workspace_path or not workspace_path.is_dir():
        return ""
    exts = {
        ".py", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs", ".swift",
        ".js", ".jsx", ".ts", ".tsx", ".vue", ".c", ".h", ".cpp", ".hpp",
    }
    skip_parts = (".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build")
    n = 0
    try:
        for p in workspace_path.rglob("*"):
            if not p.is_file():
                continue
            if any(part in skip_parts for part in p.parts):
                continue
            if p.suffix.lower() not in exts:
                continue
            rel = p.relative_to(workspace_path)
            if str(rel).startswith("docs" + os.sep) or str(rel).startswith("docs/"):
                continue
            n += 1
            if n >= 200:
                break
    except OSError:
        return ""
    if n < 5:
        return ""
    return (
        f"\n\nWorkspace observation: There are at least {n} existing source files on disk "
        f"before generation (excluding docs/ and common dependency dirs). "
        f"This often indicates an imported or cloned project rather than an empty greenfield workspace."
    )


def _heuristic_delivery_mode(vision: str) -> Optional[str]:
    """Fast path for obvious import-iterate language (no LLM). Returns 'import_iterate' or None."""
    v = vision.lower().strip()
    if vision.strip().lower().startswith("[import]"):
        return "import_iterate"
    signals = (
        "existing codebase",
        "existing code",
        "current codebase",
        "my codebase",
        "this codebase",
        "our codebase",
        "the codebase",
        "this repo",
        "our repo",
        "the repository",
        "already have code",
        "already has code",
        "import my",
        "import this",
        "iterate on",
        "iterating on",
        "legacy application",
        "legacy codebase",
        "current application",
        "deployed application",
        "production code",
        "in this project",
        "in our project",
        "modify the existing",
        "update the existing",
        "refactor the existing",
        "bug in our",
        "fix our app",
    )
    if any(s in v for s in signals):
        return "import_iterate"
    return None


class MetaAgent:
    """Meta Agent that generates dynamic agent configurations based on project vision"""
    
    def __init__(self, budget_tracker=None):
        """
        Initialize Meta Agent
        
        Args:
            budget_tracker: Optional budget tracker instance
        """
        self.budget_tracker = budget_tracker

        # Load skills/tools from config for all meta sub-agents
        meta_tools: list = []
        try:
            config = ConfigLoader.load()
            entries = config.tools.global_tools + config.tools.agent_tools.get("meta", [])
            meta_tools = load_tools(entries)
            has_git = any(
                getattr(t, "metadata", None) is not None
                and getattr(t.metadata, "name", None) == "git"
                for t in meta_tools
            )
            if not has_git:
                meta_tools = [GitTool, *meta_tools]
            logger.info("MetaAgent: loaded %d extra tool(s) from config", len(meta_tools))
        except Exception:
            logger.warning("MetaAgent: failed to load extra tools — continuing without", exc_info=True)

        # Create vision agent
        vision_backstory = load_prompt('vision_agent.txt', fallback="""You are the Vision Agent.
Your goal is to analyze the raw project vision/idea and extract the core "Project DNA" to guide the team.
Responsibilities:
1. Identify the Target Audience and their specific pain points.
2. Define the Product Tone (e.g., "Professional yet simple", "Playful", "Corporate").
3. Extract Key Constraints (e.g., "Australian Law", "Mobile First").
4. Summarize the Ultimate Value Prop.

Output Format: A concise textual summary titled "Project Context Digest".""")
        
        vision_tool_hint = ""
        if meta_tools:
            vision_tool_hint = (
                "\n\nYou have access to tools including `git` (clone, init, status, …) and "
                "`skill_query` when present. Use `git` to fetch or inspect repositories when "
                "the vision references a URL; use skill_query for framework patterns."
            )

        self.vision_agent = BaseLlamaIndexAgent(
            role='Visionary',
            goal='Extract the core DNA and requirements from the project vision',
            backstory=vision_backstory + vision_tool_hint,
            tools=list(meta_tools),
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

        triage_backstory = load_prompt(
            'delivery_mode_triage_agent.txt',
            fallback="""You are a delivery-mode router for an AI software studio.
Decide if the user should use GREENFIELD mode (generate a brand-new project from description)
or IMPORT_ITERATE mode (upload existing source, run analysis, then Refine/chat edits in place).

Rules:
- GREENFIELD: New app/service from scratch, PoC, "build me a", sample project, tutorial-style request.
  Reference repos attached as *examples only* (Repomix) still mean GREENFIELD if they ask to "create" or "build" something new modeled on those patterns.
- IMPORT_ITERATE: Work on *their* existing product/code: bugfixes, features on current app, "our codebase", "this repo",
  "legacy system", "extend what we have", "clone X and modify the actual project files", migrations in-place.

Output ONLY valid JSON (no markdown):
{"delivery_mode":"greenfield"|"import_iterate","confidence":"high"|"medium"|"low","reason":"one sentence"}""",
        )
        self._triage_agent = BaseLlamaIndexAgent(
            role='Delivery Mode Router',
            goal='Classify greenfield vs import-and-iterate from the user request',
            backstory=triage_backstory + "\n\nIMPORTANT: Do not use tools. Output only the JSON object.",
            tools=[],
            agent_type="manager",
            budget_tracker=budget_tracker,
            verbose=False,
        )

    def triage_delivery_mode(
        self,
        vision_for_triage: str,
        workspace_path: Optional[Path] = None,
    ) -> Dict[str, str]:
        """
        LLM + light heuristics: greenfield vs import_iterate.

        vision_for_triage should exclude Repomix/reference blocks (see user_vision_for_triage).
        """
        hint = workspace_source_hint(workspace_path) if workspace_path else ""
        heuristic = _heuristic_delivery_mode(vision_for_triage)
        if heuristic == "import_iterate":
            out = {
                "delivery_mode": "import_iterate",
                "confidence": "high",
                "reason": "Strong import/iterate signals in the request wording.",
            }
            logger.info("Delivery triage (heuristic): %s", out)
            return out

        prompt = f"""Classify this user request.

{vision_for_triage}{hint}

Return only JSON: {{"delivery_mode":"greenfield"|"import_iterate","confidence":"high"|"medium"|"low","reason":"..."}}"""

        response_str = str(self._triage_agent.chat(prompt)).strip()
        try:
            if "```json" in response_str:
                json_start = response_str.find("```json") + 7
                json_end = response_str.find("```", json_start)
                json_str = response_str[json_start:json_end].strip()
            elif "```" in response_str:
                json_start = response_str.find("```") + 3
                json_end = response_str.find("```", json_start)
                json_str = response_str[json_start:json_end].strip()
            else:
                json_str = response_str
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Delivery triage JSON parse failed: %s — defaulting to greenfield", e)
            return {"delivery_mode": "greenfield", "confidence": "low", "reason": "Triage parse failed; continuing as greenfield."}

        mode = str(data.get("delivery_mode", "greenfield")).lower()
        if mode not in ("greenfield", "import_iterate"):
            mode = "greenfield"
        conf = str(data.get("confidence", "low")).lower()
        if conf not in ("high", "medium", "low"):
            conf = "low"
        reason = str(data.get("reason", "")).strip() or "No reason given."
        out = {"delivery_mode": mode, "confidence": conf, "reason": reason}
        logger.info("Delivery triage (LLM): %s", out)
        return out
    
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

SKILL KNOWLEDGE (auto-injected — do NOT tell agents to call skill_query):
Framework-specific skills are automatically fetched and injected into the Designer 
and Tech Architect task prompts. Their outputs (design_spec.md, tech_stack.md) will 
contain framework-specific conventions, file structures, and coding patterns. 
Downstream agents (Developer, Frontend, Code Reviewer) inherit this knowledge by 
reading those artifacts. Do NOT instruct any agent to call skill_query — it happens 
automatically. Instead, instruct agents to carefully follow the conventions in 
design_spec.md and tech_stack.md.

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
            
            # Save to file (use thread-local workspace if available)
            from ..tools.file_tools import _resolve_workspace
            workspace_path = _resolve_workspace()
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
