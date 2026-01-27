"""
Vision Refinement Workflow for improving requirements quality
"""
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from ..crews.ba_crew import BACrew
from ..crews.requirements_reviewer_crew import RequirementsReviewerCrew

logger = logging.getLogger(__name__)


class VisionRefinementWorkflow:
    """Multi-step clarification loop with reviewer agent for vision refinement"""
    
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.ba_crew = BACrew()
        self.reviewer_crew = RequirementsReviewerCrew()
    
    def refine_vision(self, user_vision: str, design_specs: str = "") -> Dict[str, Any]:
        """
        Refine vision through multi-step process:
        1. Initial BA analysis
        2. Critical review
        3. Generate clarification questions if needed
        4. Refine requirements with feedback
        """
        logger.info("Starting vision refinement workflow")
        
        # Step 1: Initial analysis by BA
        logger.info("Step 1: Initial BA analysis")
        initial_requirements = self._run_initial_analysis(user_vision, design_specs)
        
        # Step 2: Critical review
        logger.info("Step 2: Critical review by requirements reviewer")
        review_result = self._run_review(initial_requirements, user_vision)
        
        # Step 3: Check if clarification is needed
        if review_result.get('needs_clarification', False):
            logger.info("Step 3: Clarification needed - generating questions")
            clarification_questions = self._generate_clarification_questions(
                user_vision,
                review_result
            )
            
            return {
                'requirements': initial_requirements,
                'review': review_result,
                'needs_clarification': True,
                'clarification_questions': clarification_questions,
                'refined': False
            }
        else:
            logger.info("Step 3: No clarification needed - requirements are clear")
            return {
                'requirements': initial_requirements,
                'review': review_result,
                'needs_clarification': False,
                'refined': True
            }
    
    def refine_with_responses(self, user_vision: str, initial_requirements: str, 
                             user_responses: Dict[str, str], design_specs: str = "") -> Dict[str, Any]:
        """Refine requirements with user responses to clarification questions"""
        logger.info("Refining requirements with user responses")
        
        # Build enhanced vision with user responses
        enhanced_vision = f"{user_vision}\n\nClarifications:\n"
        for question, answer in user_responses.items():
            enhanced_vision += f"Q: {question}\nA: {answer}\n\n"
        
        # Re-run BA analysis with enhanced vision
        refined_requirements = self._run_initial_analysis(enhanced_vision, design_specs)
        
        # Re-review
        review_result = self._run_review(refined_requirements, enhanced_vision)
        
        return {
            'requirements': refined_requirements,
            'review': review_result,
            'refined': True
        }
    
    def _run_initial_analysis(self, vision: str, design_specs: str) -> str:
        """Run initial BA analysis"""
        try:
            # Read existing requirements if available
            requirements_file = self.workspace_path / "requirements.md"
            if requirements_file.exists():
                with open(requirements_file, 'r') as f:
                    existing_requirements = f.read()
            else:
                existing_requirements = ""
            
            # Run BA crew
            ba_result = self.ba_crew.run(vision=vision, design_specs=design_specs)
            
            # Read the generated requirements
            if requirements_file.exists():
                with open(requirements_file, 'r') as f:
                    return f.read()
            else:
                return str(ba_result)
        except Exception as e:
            logger.error(f"BA analysis failed: {e}")
            return f"Error in BA analysis: {str(e)}"
    
    def _run_review(self, requirements: str, vision: str) -> Dict[str, Any]:
        """Run requirements review"""
        try:
            review_result = self.reviewer_crew.run(requirements=requirements, vision=vision)
            return review_result
        except Exception as e:
            logger.error(f"Requirements review failed: {e}")
            return {
                'review_result': f"Error in review: {str(e)}",
                'needs_clarification': False,
                'gaps': [],
                'ambiguities': [],
                'technical_concerns': []
            }
    
    def _generate_clarification_questions(self, vision: str, review_result: Dict[str, Any]) -> list:
        """Generate targeted clarification questions based on review findings"""
        questions = []
        
        # Questions based on gaps
        gaps = review_result.get('gaps', [])
        for gap in gaps[:3]:  # Limit to top 3 gaps
            questions.append(f"Can you clarify: {gap}?")
        
        # Questions based on ambiguities
        ambiguities = review_result.get('ambiguities', [])
        for ambiguity in ambiguities[:3]:  # Limit to top 3 ambiguities
            questions.append(f"This requirement seems ambiguous: {ambiguity}. Can you provide more details?")
        
        # Generic questions if no specific issues found
        if not questions:
            questions = [
                "Are there any specific performance requirements?",
                "What are the expected user roles and permissions?",
                "Are there any integration requirements with external systems?"
            ]
        
        return questions[:5]  # Limit to 5 questions

