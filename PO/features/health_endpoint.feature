Feature: Health Endpoint
  As a system administrator, I need to check the health status of the application.

  Scenario: Check service status when service is up
    Given a healthy application
    When I send a GET request to "/health"
    Then the response status should be 200 OK
    And the response should contain JSON with key "status" set to "up"

  Scenario: Check service status when service is down
    Given a unhealthy application
    When I send a GET request to "/health"
    Then the response status should be 500 Internal Server Error
    And the response should contain JSON with key "status" set to "down"