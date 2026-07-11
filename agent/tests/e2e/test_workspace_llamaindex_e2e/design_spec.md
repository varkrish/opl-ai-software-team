# Design Specification

## Project Overview
This design implements a simple Python calculator with basic arithmetic operations (addition and subtraction). It follows clean code principles and includes comprehensive unit testing using pytest.

## Main Components

1. **Calculator Core (calculator module)**
   - **Calculator Class**
     - `add(a, b)` method
     - `subtract(a, b)` method
   - Implements basic arithmetic operations
   - Handles zero, positive, and negative numbers

2. **Testing (tests module)**
   - **Test Cases**
     - Test valid cases
     - Test edge cases (zero, negative numbers)
     - Test invalid inputs (non-numeric types)

## Data Flow
- Arithmetic operations flow directly through the Calculator class methods
- Test cases call Calculator methods and verify results

## Interface Contracts

### Calculator Class