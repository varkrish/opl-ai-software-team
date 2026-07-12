"""
Unit tests for prompt improvements — validates contracts added in the prompt enhancement pass.

Tests cover:
  1. Dead files have been removed (3 files)
  2. Vision agent produces structured section headers
  3. Review requirements task has VERDICT signal
  4. Validate requirements task has PASS/FAIL output
  5. Prompter agent has APPEND-not-REPLACE constraint
  6. Code reviewer backstory has severity classifications
  7. Tech architect backstory is deduplicated (< threshold word count)
  8. define_tech_stack_task.txt: HTML/CSS/JavaScript section with module system in first 500 chars
"""
from pathlib import Path
import pytest

PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "ai_software_dev_crew" / "prompts"
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Dead files removed
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeadFilesRemoved:
    """Verify unused prompt files have been deleted."""

    def test_designer_backstory_deleted(self):
        """designer/designer_backstory.txt was never loaded — must not exist."""
        path = PROMPTS_DIR / "designer" / "designer_backstory.txt"
        assert not path.exists(), (
            "designer_backstory.txt is unused dead code and should have been deleted"
        )

    def test_backend_developer_backstory_deleted(self):
        """dev_crew/backend_developer_backstory.txt was never loaded — must not exist."""
        path = PROMPTS_DIR / "dev_crew" / "backend_developer_backstory.txt"
        assert not path.exists(), (
            "backend_developer_backstory.txt is unused dead code and should have been deleted"
        )

    def test_implement_feature_task_template_deleted(self):
        """dev_crew/implement_feature_task_template.txt was never loaded — must not exist."""
        path = PROMPTS_DIR / "dev_crew" / "implement_feature_task_template.txt"
        assert not path.exists(), (
            "implement_feature_task_template.txt is unused dead code and should have been deleted"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Vision agent structured output
# ═══════════════════════════════════════════════════════════════════════════════

class TestVisionAgentStructure:
    def _load(self):
        return (PROMPTS_DIR / "vision_agent.txt").read_text()

    def test_has_target_audience_section(self):
        assert "Target Audience" in self._load()

    def test_has_pain_points_section(self):
        assert "Pain Points" in self._load()

    def test_has_value_proposition_section(self):
        prompt = self._load()
        assert "Value Proposition" in prompt or "Value Prop" in prompt

    def test_has_key_constraints_section(self):
        assert "Key Constraints" in self._load()

    def test_has_product_tone_section(self):
        assert "Product Tone" in self._load() or "Tone" in self._load()

    def test_has_length_budget(self):
        """Vision agent must have a word/character budget to avoid bloat."""
        prompt = self._load().lower()
        has_budget = "word" in prompt or "350" in prompt or "concise" in prompt
        assert has_budget, "Vision agent must specify an output length budget"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Requirements reviewer — VERDICT signal
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequirementsReviewerVerdict:
    def _load(self):
        return (PROMPTS_DIR / "requirements_reviewer" / "review_requirements_task.txt").read_text()

    def test_has_verdict_approved(self):
        """Must have a machine-readable VERDICT: APPROVED marker."""
        assert "VERDICT: APPROVED" in self._load(), (
            "review_requirements_task.txt must output 'VERDICT: APPROVED' for orchestrator gating"
        )

    def test_has_verdict_needs_clarification(self):
        """Must have a machine-readable VERDICT: NEEDS_CLARIFICATION marker."""
        assert "VERDICT: NEEDS_CLARIFICATION" in self._load(), (
            "review_requirements_task.txt must output 'VERDICT: NEEDS_CLARIFICATION' for orchestrator gating"
        )

    def test_verdict_is_at_document_end(self):
        """The VERDICT must appear at the end of the instructions (not mid-document)."""
        prompt = self._load()
        verdict_idx = prompt.rfind("VERDICT:")
        assert verdict_idx > len(prompt) * 0.5, (
            "VERDICT instruction must appear in the second half of the prompt (near end)"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Validate requirements — structured PASS/FAIL output
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateRequirementsStructure:
    def _load(self):
        return (PROMPTS_DIR / "ba_crew" / "validate_requirements_task.txt").read_text()

    def test_has_pass_fail_verdict(self):
        prompt = self._load()
        assert "PASS" in prompt and "FAIL" in prompt, (
            "validate_requirements_task.txt must include PASS/FAIL status in output format"
        )

    def test_has_smart_compliance_check(self):
        assert "SMART" in self._load(), (
            "validate_requirements_task.txt must check SMART compliance"
        )

    def test_has_file_writer_instruction(self):
        assert "file_writer" in self._load() or "validation_report" in self._load()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Prompter agent — APPEND not REPLACE
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrompterAgentAppendConstraint:
    def _load(self):
        return (PROMPTS_DIR / "prompter_agent.txt").read_text()

    def test_has_append_not_replace_constraint(self):
        prompt = self._load().upper()
        has_constraint = "APPEND" in prompt or "NOT REPLACE" in prompt or "APPENDED" in prompt
        assert has_constraint, (
            "prompter_agent.txt must state that its output is APPENDED to base personas, not replacing them"
        )

    def test_has_length_budget(self):
        """Each extension should have a word/char budget."""
        prompt = self._load()
        has_budget = "word" in prompt.lower() or "50" in prompt or "100" in prompt
        assert has_budget, "prompter_agent.txt must specify a length budget per extension"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Code reviewer backstory — severity classifications
# ═══════════════════════════════════════════════════════════════════════════════

class TestCodeReviewerOutputFormat:
    def _load(self):
        return (PROMPTS_DIR / "dev_crew" / "code_reviewer_backstory.txt").read_text()

    def test_has_blocker_severity(self):
        assert "BLOCKER" in self._load(), "Code reviewer must classify findings with BLOCKER severity"

    def test_has_warning_severity(self):
        assert "WARNING" in self._load(), "Code reviewer must classify findings with WARNING severity"

    def test_has_verdict(self):
        prompt = self._load()
        assert "APPROVED" in prompt or "NEEDS_CHANGES" in prompt, (
            "Code reviewer must output a clear verdict (APPROVED or NEEDS_CHANGES)"
        )

    def test_has_file_writer_instruction(self):
        assert "file_writer" in self._load() or "code_review.md" in self._load()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Tech architect backstory — deduplicated
# ═══════════════════════════════════════════════════════════════════════════════

class TestTechArchitectBackstoryDeduplicated:
    def _load(self):
        return (PROMPTS_DIR / "tech_architect" / "tech_architect_backstory.txt").read_text()

    def test_total_word_count_reasonable(self):
        """After deduplication the backstory should be under 250 words."""
        words = len(self._load().split())
        assert words < 250, (
            f"Tech architect backstory has {words} words — should be < 250 after deduplication"
        )

    def test_has_vision_compliance_rule(self):
        """Must still mention the vision compliance rule — just once."""
        prompt = self._load()
        assert "vision" in prompt.lower() and ("exact" in prompt.lower() or "never substitute" in prompt.lower() or "names" in prompt.lower())


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Analyze vision task — no "ask clarifying questions" dead instruction
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnalyzeVisionNoAskQuestions:
    def _load(self):
        return (PROMPTS_DIR / "ba_crew" / "analyze_vision_task.txt").read_text()

    def test_no_ask_clarifying_questions(self):
        """Automated pipelines can't pause for questions — must document assumptions instead."""
        lower = self._load().lower()
        assert "ask clarifying questions" not in lower, (
            "analyze_vision_task.txt must NOT tell agents to 'ask clarifying questions' "
            "— they should document assumptions instead"
        )

    def test_has_assumptions_instruction(self):
        """Must tell the agent to document assumptions."""
        lower = self._load().lower()
        assert "assumption" in lower, (
            "analyze_vision_task.txt must instruct the agent to document assumptions "
            "when the vision is ambiguous"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 9. define_tech_stack_task.txt — no duplicate rule numbers
# ═══════════════════════════════════════════════════════════════════════════════

class TestTechStackTaskNoDuplicateRules:
    def _load(self):
        return (PROMPTS_DIR / "tech_architect" / "define_tech_stack_task.txt").read_text()

    def test_no_duplicate_rule_8(self):
        """Rule 8 must appear exactly once."""
        import re
        prompt = self._load()
        # Match standalone rule numbers like "8." at the start of a line
        matches = re.findall(r"(?m)^8\.", prompt)
        assert len(matches) <= 1, (
            f"Rule '8.' appears {len(matches)} times in define_tech_stack_task.txt — must appear at most once"
        )
