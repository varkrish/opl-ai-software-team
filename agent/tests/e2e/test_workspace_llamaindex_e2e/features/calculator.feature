Feature: Calculator
  Scenario: Add two numbers
    Given a valid input of two integers
    When I call the add method with these integers
    Then the result should be the sum of the two integers

  Scenario Outline: Add numbers with various cases
    Given two numbers <a> and <b>
    When I add them
    Then the result should be <result>

    Examples:
      | a | b | result |
      | 1 | 2 | 3      |
      | 0 | 5 | 5      |
      | -3| 4 | 1      |
      | 5 | -2| 3      |

  Scenario: Subtract two numbers
    Given a valid input of two integers
    When I call the subtract method with these integers
    Then the result should be the difference between the two integers

  Scenario Outline: Subtract numbers with various cases
    Given two numbers <a> and <b>
    When I subtract <b> from <a>
    Then the result should be <result>

    Examples:
      | a | b | result |
      | 5 | 3 | 2      |
      | 2 | 5 | -3     |
      | 0 | 0 | 0      |
      | -2| 3 | -5     |

  Scenario: Handle invalid inputs
    Given non-integer inputs
    When I attempt to add or subtract
    Then an error should be raised
