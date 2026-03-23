Feature: Validation Issue Lifecycle
  As a development workflow
  I need to persist validation issues in the database
  So that issues are tracked and can be acted upon by agents

  Background:
    Given a job "job-val-1" exists in the database

  Scenario: Creating a validation issue stores it with pending status
    When I create a validation issue for "job-val-1" with check "syntax" and severity "error"
    Then the issue should exist in the database with status "pending"
    And the issue should have a "created_at" timestamp
    And the "completed_at" field should be null

  Scenario: Getting issues filters by job_id
    Given validation issues exist for "job-val-1" and "job-val-2"
    When I get validation issues for "job-val-1"
    Then only issues belonging to "job-val-1" are returned

  Scenario: Getting issues filters by check_name
    Given validation issues with checks "syntax" and "imports" exist for "job-val-1"
    When I get validation issues for "job-val-1" filtered by check "syntax"
    Then only issues with check_name "syntax" are returned

  Scenario: Getting pending issues excludes completed ones
    Given a "pending" issue and a "completed" issue exist for "job-val-1"
    When I get pending validation issues for "job-val-1"
    Then only the "pending" issue is returned

  Scenario: Updating issue status to completed sets completed_at
    Given a validation issue "vi-001" exists with status "pending"
    When I update issue "vi-001" status to "completed"
    Then the issue status should be "completed"
    And "completed_at" should be set

  Scenario: Updating issue with fix_strategy persists it
    Given a validation issue "vi-002" exists with status "running"
    When I update issue "vi-002" with fix_strategy "Add missing import for flask"
    Then the issue should have fix_strategy "Add missing import for flask"

  Scenario: Getting failed issues returns only failed status
    Given issues with statuses "pending", "completed", and "failed" exist
    When I get failed validation issues for "job-val-1"
    Then only issues with status "failed" are returned

  Scenario: Deleting issues for a job removes all related rows
    Given 3 validation issues exist for "job-val-1"
    When I delete validation issues for "job-val-1"
    Then no validation issues remain for "job-val-1"
