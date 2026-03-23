Feature: Validation API Endpoint
  As a frontend application
  I need a REST endpoint to fetch validation results
  So that I can display issue details and fix status to the user

  Scenario: GET /api/jobs/{id}/validation returns issues with summary
    Given job "job-api-1" has 3 validation issues (1 completed, 1 failed, 1 pending)
    When I GET /api/jobs/job-api-1/validation
    Then the response status should be 200
    And the response should contain "issues" with 3 entries
    And the "summary" should show total=3, fixed=1, failed=1, pending=1
    And "overall" should be "ISSUES_FOUND"

  Scenario: Job with no validation issues returns empty list
    Given job "job-api-2" exists but has no validation issues
    When I GET /api/jobs/job-api-2/validation
    Then the response status should be 200
    And the response should contain "issues" with 0 entries
    And "overall" should be "PASS"

  Scenario: Nonexistent job returns 404
    When I GET /api/jobs/nonexistent/validation
    Then the response status should be 404
