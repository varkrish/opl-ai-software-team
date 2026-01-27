"""
Requirements Reviewer Crew for quality assurance of requirements
"""
from crewai import Agent, Task, Crew
from typing import Dict, Any, Optional
from ..utils.llm_config import get_llm_for_agent
from ..utils.prompt_loader import load_prompt
from ..tools.file_operations import FileWriterTool, FileReaderTool


class RequirementsReviewerCrew:
    """Requirements Reviewer Crew for validating requirements quality"""
    
    def __init__(self, custom_backstories: Dict[str, str] = None):
        self._custom_backstories = custom_backstories or {}
    
    def requirements_reviewer(self) -> Agent:
        default_backstory = load_prompt('requirements_reviewer/requirements_reviewer_backstory.txt') or """You are a principal software architect with 20+ years of experience. 
        You have seen countless projects fail due to poor requirements. Your job is to be the "devil's advocate" 
        and find every possible issue before code is written. You critically analyze requirements documents to 
        identify gaps, ambiguities, and technical issues before development begins."""
        
        backstory = self._custom_backstories.get('requirements_reviewer', default_backstory)
        
        return Agent(
            role='Requirements Quality Reviewer',
            goal='Critically analyze requirements documents to identify gaps, ambiguities, and technical issues before development begins',
            backstory=backstory,
            llm=get_llm_for_agent("reviewer"),
            verbose=True,
            allow_delegation=False,
            tools=[FileReaderTool(), FileWriterTool()]
        )
    
    def review_requirements(self) -> Task:
        task_desc = load_prompt('requirements_reviewer/review_requirements_task.txt') or """Review the requirements document with extreme scrutiny.
        
        Requirements Document: {requirements}
        User Vision: {vision}
        
        Review Criteria:
        1. Ambiguity Check:
           - Look for vague terms like "fast", "user-friendly", "secure"
           - Identify undefined business rules
           - Flag unclear data formats or protocols
        
        2. Completeness Check:
           - Missing error handling scenarios
           - Undefined user roles and permissions
           - Missing integration points
           - Absent non-functional requirements
        
        3. Technical Feasibility:
           - Check if proposed technology stack can deliver requirements
           - Identify potential performance bottlenecks
           - Flag security or compliance issues
        
        4. Testability Check:
           - Ensure all requirements are testable and measurable
           - Verify Gherkin scenarios are properly structured
           - Check for missing edge cases
        
        Output your review findings in a structured format:
        - Needs Clarification: Yes/No
        - Identified Gaps: [list of gaps]
        - Ambiguities: [list of ambiguous requirements]
        - Technical Concerns: [list of technical issues]
        - Recommendations: [list of recommendations]
        
        If clarification is needed, generate specific questions that should be asked to the user.
        Write your review to 'requirements_review.md' file."""
        
        return Task(
            description=task_desc,
            agent=self.requirements_reviewer()
        )
    
    def run(self, requirements: str, vision: str) -> Dict[str, Any]:
        """Run requirements review"""
        review_task = self.review_requirements()
        review_task.description = review_task.description.format(
            requirements=requirements,
            vision=vision
        )
        
        crew = Crew(
            agents=[self.requirements_reviewer()],
            tasks=[review_task],
            verbose=True
        )
        
        result = crew.kickoff()
        
        return {
            'review_result': str(result),
            'needs_clarification': self._parse_clarification_needed(str(result)),
            'gaps': self._parse_gaps(str(result)),
            'ambiguities': self._parse_ambiguities(str(result)),
            'technical_concerns': self._parse_technical_concerns(str(result))
        }
    
    def _parse_clarification_needed(self, review_text: str) -> bool:
        """Parse if clarification is needed from review text"""
        review_lower = review_text.lower()
        if 'needs clarification: yes' in review_lower or 'clarification needed: yes' in review_lower:
            return True
        if 'needs clarification: no' in review_lower or 'clarification needed: no' in review_lower:
            return False
        # Heuristic: if gaps or ambiguities are mentioned, likely needs clarification
        if 'gap' in review_lower or 'ambiguous' in review_lower or 'unclear' in review_lower:
            return True
        return False
    
    def _parse_gaps(self, review_text: str) -> list:
        """Parse identified gaps from review text"""
        gaps = []
        lines = review_text.split('\n')
        in_gaps_section = False
        
        for line in lines:
            if 'identified gaps' in line.lower() or 'gaps:' in line.lower():
                in_gaps_section = True
                continue
            if in_gaps_section:
                if line.strip().startswith('-') or line.strip().startswith('*'):
                    gaps.append(line.strip()[1:].strip())
                elif line.strip() and not line.strip().startswith('#'):
                    if ':' in line or 'ambiguities' in line.lower():
                        in_gaps_section = False
                    else:
                        gaps.append(line.strip())
        
        return gaps[:10]  # Limit to 10 gaps
    
    def _parse_ambiguities(self, review_text: str) -> list:
        """Parse ambiguities from review text"""
        ambiguities = []
        lines = review_text.split('\n')
        in_ambiguities_section = False
        
        for line in lines:
            if 'ambiguities:' in line.lower() or 'ambiguous' in line.lower() and ':' in line:
                in_ambiguities_section = True
                continue
            if in_ambiguities_section:
                if line.strip().startswith('-') or line.strip().startswith('*'):
                    ambiguities.append(line.strip()[1:].strip())
                elif line.strip() and not line.strip().startswith('#'):
                    if ':' in line and 'technical' in line.lower():
                        in_ambiguities_section = False
                    else:
                        ambiguities.append(line.strip())
        
        return ambiguities[:10]  # Limit to 10 ambiguities
    
    def _parse_technical_concerns(self, review_text: str) -> list:
        """Parse technical concerns from review text"""
        concerns = []
        lines = review_text.split('\n')
        in_concerns_section = False
        
        for line in lines:
            if 'technical concerns' in line.lower() or 'technical issues' in line.lower():
                in_concerns_section = True
                continue
            if in_concerns_section:
                if line.strip().startswith('-') or line.strip().startswith('*'):
                    concerns.append(line.strip()[1:].strip())
                elif line.strip() and not line.strip().startswith('#'):
                    if ':' in line and ('recommendations' in line.lower() or 'summary' in line.lower()):
                        in_concerns_section = False
                    else:
                        concerns.append(line.strip())
        
        return concerns[:10]  # Limit to 10 concerns

