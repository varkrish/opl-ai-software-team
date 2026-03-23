Feature: Job Status After Validation
  As the workflow orchestrator
  I need to set the final job status based on validation results
  So that the user knows whether the generated code is production-ready

  Scenario: Job with all issues resolved is marked completed
    Given the validation phase found 3 issues
    And all 3 issues were remediated successfully (status=completed)
    When the workflow finishes
    Then the job status should be "completed"

  Scenario: Job with unresolved error-severity issues is marked failed
    Given the validation phase found 2 error-severity issues
    And 1 issue was remediated but 1 remains failed
    When the workflow finishes
    Then the job status should be "failed"
    And the error message should mention "1 unresolved issue(s)"

  Scenario: Warning-severity issues do not block job completion
    Given the validation phase found 2 warning-severity issues
    And both warnings have status "pending" (not remediated)
    When the workflow finishes
    Then the job status should be "completed"
    And the warnings should be recorded in the database for visibility
