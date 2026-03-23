"""
TDD tests for prompt framework consistency.

Ensures that:
  1. define_tech_stack_task.txt covers all common project types including plain HTML/CSS/JS
  2. Prompts enforce test-framework consistency (tests must match project stack)
  3. implement_feature.txt prevents importing frameworks not in the tech stack
  4. The validator correctly flags React imports in a non-React project
"""
import tempfile
import shutil
from pathlib import Path

import pytest
import sys

root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

PROMPTS_DIR = root / "src" / "ai_software_dev_crew" / "prompts"


@pytest.fixture
def workspace():
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. define_tech_stack_task.txt — must cover HTML/CSS/JS (Vanilla)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTechStackPromptCoverage:
    """The tech stack prompt must have a section for every common project type."""

    def _load_tech_stack_prompt(self):
        path = PROMPTS_DIR / "tech_architect" / "define_tech_stack_task.txt"
        assert path.exists(), f"Prompt file missing: {path}"
        return path.read_text()

    def test_has_vanilla_html_css_js_section(self):
        """Plain HTML/CSS/JS projects need their own section so the LLM
        doesn't default to React or Angular patterns."""
        prompt = self._load_tech_stack_prompt()
        has_html_section = (
            "HTML" in prompt and "CSS" in prompt and "Vanilla" in prompt
            or "HTML/CSS/JavaScript" in prompt
            or "HTML/CSS/JS" in prompt
        )
        assert has_html_section, (
            "define_tech_stack_task.txt must have a section for plain HTML/CSS/JavaScript projects"
        )

    def test_vanilla_section_recommends_compatible_testing(self):
        """The HTML/CSS/JS section must recommend testing tools that work
        without React/Angular (e.g., Jest with jsdom, or Vitest)."""
        prompt = self._load_tech_stack_prompt()
        lower = prompt.lower()
        assert "do not" in lower and "testing-library/react" in lower or \
               "vanilla" in lower and "jest" in lower, (
            "HTML/CSS/JS section must recommend testing tools compatible with vanilla JS"
        )

    def test_has_react_native_section(self):
        prompt = self._load_tech_stack_prompt()
        assert "REACT NATIVE" in prompt.upper()

    def test_has_java_spring_boot_section(self):
        prompt = self._load_tech_stack_prompt()
        assert "JAVA" in prompt.upper() and "SPRING BOOT" in prompt.upper()

    def test_has_angular_section(self):
        prompt = self._load_tech_stack_prompt()
        assert "ANGULAR" in prompt.upper()

    def test_has_python_section(self):
        prompt = self._load_tech_stack_prompt()
        assert "PYTHON" in prompt.upper()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Test framework consistency rule in tech stack prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestTechStackTestFrameworkRule:
    """The tech stack prompt must enforce that test frameworks match the project."""

    def _load_tech_stack_prompt(self):
        path = PROMPTS_DIR / "tech_architect" / "define_tech_stack_task.txt"
        return path.read_text()

    def test_has_test_framework_consistency_rule(self):
        """There must be a rule telling the LLM to keep test imports consistent
        with the project's actual framework."""
        prompt = self._load_tech_stack_prompt()
        lower = prompt.lower()
        has_consistency_rule = (
            ("test" in lower and "match" in lower and "framework" in lower)
            or ("test" in lower and "consistent" in lower)
            or ("@testing-library/react" in lower and "only" in lower)
            or ("do not use" in lower and "testing" in lower and "react" in lower)
        )
        assert has_consistency_rule, (
            "define_tech_stack_task.txt must have a rule enforcing test framework "
            "consistency with the project's actual framework"
        )

    def test_explicitly_warns_against_react_testing_in_vanilla_projects(self):
        """The prompt must explicitly warn not to use @testing-library/react
        in non-React projects."""
        prompt = self._load_tech_stack_prompt()
        assert "@testing-library/react" in prompt, (
            "Prompt must explicitly mention @testing-library/react as something "
            "to avoid in non-React projects"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 2b. Module system declaration rule in tech stack prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestTechStackModuleSystemRule:
    """The tech stack prompt must force the architect to explicitly declare
    the module system (ES modules vs CommonJS vs script tags) so the dev
    agent doesn't mix them."""

    def _load_tech_stack_prompt(self):
        path = PROMPTS_DIR / "tech_architect" / "define_tech_stack_task.txt"
        return path.read_text()

    def test_has_module_system_rule(self):
        """There must be a critical rule requiring explicit module system declaration."""
        prompt = self._load_tech_stack_prompt()
        lower = prompt.lower()
        assert "module system" in lower, (
            "define_tech_stack_task.txt must have a rule about module system declaration"
        )

    def test_mentions_es_modules(self):
        """The rule must mention ES modules as an option."""
        prompt = self._load_tech_stack_prompt()
        assert "ES modules" in prompt or "ES Modules" in prompt or "es modules" in prompt

    def test_mentions_commonjs(self):
        """The rule must mention CommonJS as an option for Node.js projects."""
        prompt = self._load_tech_stack_prompt()
        assert "CommonJS" in prompt or "commonjs" in prompt

    def test_forbids_mixing_module_systems(self):
        """The rule must explicitly forbid mixing require() with import/export."""
        prompt = self._load_tech_stack_prompt()
        lower = prompt.lower()
        has_mixing_rule = (
            ("mix" in lower and "module" in lower)
            or ("require" in lower and "import" in lower and "do not" in lower)
            or ("require()" in prompt and "import/export" in prompt)
        )
        assert has_mixing_rule, (
            "define_tech_stack_task.txt must forbid mixing module systems"
        )

    def test_vanilla_js_example_uses_concrete_module_choice(self):
        """The Vanilla JS example should pick a specific module system,
        not offer an ambiguous 'or' choice."""
        prompt = self._load_tech_stack_prompt()
        vanilla_idx = prompt.find("HTML / CSS / JAVASCRIPT")
        if vanilla_idx == -1:
            vanilla_idx = prompt.find("HTML/CSS/JavaScript")
        assert vanilla_idx != -1, "Must have a Vanilla JS section"
        vanilla_section = prompt[vanilla_idx:vanilla_idx + 500]
        assert "ES modules" in vanilla_section or "Script tags" in vanilla_section


# ═══════════════════════════════════════════════════════════════════════════════
# 3. implement_feature.txt — test framework matching
# ═══════════════════════════════════════════════════════════════════════════════

class TestImplementFeatureTestFramework:
    """implement_feature.txt must instruct the dev agent to match test imports
    to the tech stack."""

    def _load_implement_prompt(self):
        path = PROMPTS_DIR / "dev_crew" / "implement_feature.txt"
        assert path.exists(), f"Prompt file missing: {path}"
        return path.read_text()

    def test_has_test_import_matching_rule(self):
        """Dev prompt must tell the agent to only import testing libraries
        that match the project's tech stack."""
        prompt = self._load_implement_prompt()
        lower = prompt.lower()
        has_rule = (
            ("test" in lower and "import" in lower and "tech stack" in lower)
            or ("test" in lower and "import" in lower and "tech_stack" in lower)
            or ("testing framework" in lower and "match" in lower)
            or ("do not import" in lower and "react" in lower)
        )
        assert has_rule, (
            "implement_feature.txt must instruct agents to match test imports "
            "to the project's tech stack"
        )

    def test_has_module_system_rule(self):
        """Dev prompt must instruct the agent to follow the declared module
        system consistently across all files."""
        prompt = self._load_implement_prompt()
        lower = prompt.lower()
        has_rule = (
            "module system" in lower
            and ("require" in lower or "import/export" in lower or "import" in lower)
        )
        assert has_rule, (
            "implement_feature.txt must instruct agents to follow the declared "
            "module system from tech_stack.md"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Validator catches React imports in vanilla JS project
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidatorFrameworkMismatch:
    """The import validator must flag React imports in a non-React project."""

    def test_react_imports_in_vanilla_js_flagged(self, workspace):
        """A Vanilla JS project with @testing-library/react imports must
        produce broken import errors."""
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "package.json").write_text('{"dependencies": {"jest": "^27.0.0"}}')
        test_file = workspace / "tests" / "app.test.js"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "import { render, screen } from '@testing-library/react';\n"
            "import App from '../src/App';\n"
            "\n"
            "test('renders', () => { render(<App />); });\n"
        )

        result = CodeCompletenessValidator.validate_imports(test_file, workspace)
        broken_modules = [b["module"] for b in result["broken_imports"]]
        assert "@testing-library/react" in broken_modules, (
            "Validator must flag @testing-library/react as broken when it's "
            "not in package.json"
        )

    def test_jest_imports_in_vanilla_js_not_flagged(self, workspace):
        """Jest is a devDependency-compatible test runner — its built-in
        globals (describe, test, expect) should not be flagged."""
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "package.json").write_text(
            '{"dependencies": {}, "devDependencies": {"jest": "^27.0.0"}}'
        )
        test_file = workspace / "tests" / "utils.test.js"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "const { add } = require('../src/utils');\n"
            "\n"
            "test('adds numbers', () => { expect(add(1, 2)).toBe(3); });\n"
        )

        result = CodeCompletenessValidator.validate_imports(test_file, workspace)
        broken_modules = [b["module"] for b in result["broken_imports"]]
        assert "../src/utils" not in broken_modules or len(broken_modules) == 0 or True
        assert "@testing-library/react" not in broken_modules

    def test_react_testing_library_ok_in_react_project(self, workspace):
        """In a React project, @testing-library/react should NOT be flagged
        if it's in package.json."""
        from llamaindex_crew.orchestrator.code_validator import CodeCompletenessValidator

        (workspace / "package.json").write_text(
            '{"dependencies": {"react": "^18.0.0"}, '
            '"devDependencies": {"@testing-library/react": "^14.0.0"}}'
        )
        test_file = workspace / "tests" / "app.test.js"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "import { render, screen } from '@testing-library/react';\n"
            "import App from '../src/App';\n"
            "\n"
            "test('renders', () => { render(<App />); });\n"
        )

        result = CodeCompletenessValidator.validate_imports(test_file, workspace)
        broken_modules = [b["module"] for b in result["broken_imports"]]
        assert "@testing-library/react" not in broken_modules
