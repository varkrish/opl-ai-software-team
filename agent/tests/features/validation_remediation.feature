Feature: Validation Remediation Flow
  As a development workflow
  I need to auto-fix simple issues and delegate complex ones to agents
  So that generated code is corrected within the same job run

  Background:
    Given a job workspace with generated code

  Scenario: Missing __init__.py is auto-fixed without LLM call
    Given validation reports a missing "__init__.py" in "mypackage/"
    When the remediation phase runs
    Then "mypackage/__init__.py" should be created on disk
    And no LLM agent should be called
    And the issue status should be "completed"

  Scenario: Missing dependency in requirements.txt is auto-fixed
    Given validation reports undeclared dependency "flask" in requirements.txt
    When the remediation phase runs
    Then "flask" should appear in requirements.txt
    And no LLM agent should be called
    And the issue status should be "completed"

  Scenario: Syntax error triggers tech architect review then dev agent fix
    Given validation reports a syntax error in "app.py" at line 5
    When the remediation phase runs
    Then the tech architect should receive the issue for review
    And the tech architect should produce a fix strategy
    And the fix strategy should be stored in the database
    And the dev agent should receive the fix strategy and file path
    And the file should be re-validated

  Scenario: Issue resolved after fix is marked completed
    Given the dev agent has rewritten "app.py" and re-validation passes
    When the remediation loop completes for that issue
    Then the issue status should be "completed"
    And "completed_at" should be set

  Scenario: Issue still broken after fix is marked failed
    Given the dev agent has rewritten "app.py" but re-validation still fails
    When the remediation loop completes for that issue
    Then the issue status should be "failed"
    And the error field should contain the re-validation failure details
