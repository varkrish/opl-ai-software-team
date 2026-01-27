"""
Code Review Crew - Review and refine existing codebases
"""
import os
from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from ..utils import get_llm_for_agent
from ..tools import (
    FileReaderTool, FileListTool, FileWriterTool,
    PytestRunnerTool, CodeCoverageTool,
    GitStatusTool, GitCommitTool
)


def _is_git_enabled() -> bool:
    """Check if git operations are enabled"""
    git_enabled = os.getenv("ENABLE_GIT", "true").lower()
    return git_enabled in ("true", "1", "yes")


@CrewBase
class CodeReviewCrew:
    """Crew for reviewing and refining existing codebases"""

    @agent
    def code_analyzer(self) -> Agent:
        return Agent(
            role="Senior Code Analyst",
            goal="Analyze existing codebase for quality, issues, and improvement opportunities",
            backstory="""You are an expert code analyst with 20 years of experience who specializes in:
            - Identifying code smells and anti-patterns
            - Finding security vulnerabilities
            - Detecting performance bottlenecks
            - Assessing test coverage gaps
            - Reviewing architecture and design patterns
            - Finding bugs and potential runtime errors
            - Evaluating code maintainability and readability
            
            You provide detailed, actionable feedback with specific line numbers and examples.""",
            llm=get_llm_for_agent("reviewer"),
            verbose=True,
            allow_delegation=False,
            tools=[
                FileReaderTool(),
                FileListTool(),
                PytestRunnerTool(),
                CodeCoverageTool()
            ]
        )

    @agent
    def refactoring_specialist(self) -> Agent:
        return Agent(
            role="Refactoring Specialist",
            goal="Refactor code to improve quality while maintaining functionality",
            backstory="""You are a master refactoring specialist with expertise in:
            - Applying SOLID principles
            - Removing code duplication (DRY)
            - Improving naming and structure
            - Adding missing error handling
            - Improving documentation and comments
            - Optimizing performance
            - Ensuring all tests still pass after refactoring
            
            You never break existing functionality and always verify tests pass before committing changes.""",
            llm=get_llm_for_agent("worker"),
            verbose=True,
            allow_delegation=False,
            tools=[
                FileReaderTool(),
                FileWriterTool(),
                FileListTool(),
                PytestRunnerTool(),
                CodeCoverageTool(),
                *([GitStatusTool(), GitCommitTool()] if _is_git_enabled() else [])
            ]
        )

    @task
    def analyze_codebase(self) -> Task:
        return Task(
            description="""Analyze the existing codebase thoroughly.
            
            Steps:
            1. List all files in the codebase to understand structure
            2. Read key source files (prioritize main modules, core logic)
            3. Run existing tests to understand current test coverage
            4. Check code coverage percentage
            5. Identify issues in these categories:
               - **Code Smells**: Long methods, duplicate code, magic numbers, etc.
               - **Security Issues**: SQL injection risks, XSS vulnerabilities, insecure defaults
               - **Performance Problems**: Inefficient algorithms, N+1 queries, memory leaks
               - **Missing Tests**: Untested critical paths, edge cases not covered
               - **Documentation Gaps**: Missing docstrings, unclear comments, no README
               - **Architecture Issues**: Tight coupling, circular dependencies, poor separation
               - **Error Handling**: Missing try-catch, unhandled exceptions, poor error messages
               - **Code Quality**: Naming conventions, formatting, complexity
            
            6. For each issue, provide:
               - File path and line number
               - Issue description
               - Severity (Critical/High/Medium/Low)
               - Suggested fix
            
            IMPORTANT: Use the file_writer tool to save your comprehensive analysis to 'codebase_analysis_report.md'
            
            The report should be detailed and actionable.""",
            agent=self.code_analyzer(),
            expected_output="""Comprehensive codebase analysis report saved to codebase_analysis_report.md with:
            - Codebase structure overview
            - Test results and coverage
            - Detailed list of issues by category
            - Priority ranking of issues
            - Specific recommendations for each issue"""
        )

    @task
    def refactor_code(self) -> Task:
        return Task(
            description="""Refactor the codebase based on the analysis report.
            
            Read the codebase_analysis_report.md first to understand what needs to be fixed.
            
            For each high-priority issue:
            1. Read the problematic file
            2. Understand the current implementation
            3. Refactor to fix the issue:
               - Fix code smells (extract methods, remove duplication)
               - Add missing error handling
               - Improve documentation (add docstrings, comments)
               - Fix security issues
               - Optimize performance bottlenecks
               - Improve naming and structure
            4. Run tests after each change to ensure nothing breaks
            5. If tests fail, revert and try a different approach
            6. Commit changes (if git enabled) with descriptive messages like:
               - "refactor: extract duplicate code into helper function"
               - "fix: add error handling for file operations"
               - "docs: add docstrings to public methods"
               - "perf: optimize database query in user service"
               - Note: Git operations can be disabled via ENABLE_GIT=false environment variable
            
            Focus on:
            - High and Critical severity issues first
            - Maintaining backward compatibility
            - Not breaking existing functionality
            - Improving test coverage where possible
            
            IMPORTANT: After refactoring, save a summary to 'refactoring_summary.md' with:
            - List of files modified
            - Issues fixed
            - Test results (all should pass)
            - Coverage improvement (if any)""",
            agent=self.refactoring_specialist(),
            expected_output="""Refactored codebase with:
            - Improved code quality
            - Fixed security issues
            - Better error handling
            - Enhanced documentation
            - All tests passing
            - Refactoring summary report""",
            context=[self.analyze_codebase()]
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Code Review Crew"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )

