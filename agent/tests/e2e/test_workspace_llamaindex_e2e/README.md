# Calculator Project

## Overview

This project implements a simple, reusable Python calculator that provides basic arithmetic operations: addition and subtraction. The core logic lives in the `Calculator` class located in `src/calculator/calculator.py`. The project follows clean code principles, includes type hints, and is fully tested with **pytest**.

## File Structure

```
calculator_project/
├── README.md                 # Project documentation (this file)
├── requirements.txt          # Project dependencies
├── src/
│   ├── __init__.py           # Makes src a package
│   └── calculator/
│       ├── __init__.py       # Exposes Calculator class
│       └── calculator.py     # Implementation of Calculator
└── tests/
    ├── __init__.py           # Makes tests a package
    └── test_calculator.py    # pytest test suite
```

## Prerequisites

- Python **3.9** or newer
- `pip` package manager

## Installation

1. **Clone the repository** (or copy the project files into a directory).
2. Navigate to the project root:
   ```bash
   cd calculator_project
   ```
3. Install the required dependencies in a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # On Windows use `.venv\\Scripts\\activate`
   pip install -r requirements.txt
   ```

## Usage

Below is an example of how to use the `Calculator` class in your own Python code:

```python
from src.calculator import Calculator

calc = Calculator()

# Addition
result_add = calc.add(5, 3)  # Returns 8
print(f"5 + 3 = {result_add}")

# Subtraction
result_sub = calc.subtract(10, 4)  # Returns 6
print(f"10 - 4 = {result_sub}")
```

The `Calculator` methods accept any **numeric** types (`int`, `float`, `Decimal`, etc.) that support the `+` and `-` operators. Non‑numeric inputs raise a `TypeError` with a clear error message.

## Development & Testing

The project uses **pytest** for unit testing. Tests are located in the `tests/` directory.

To run the test suite and view a coverage report:

```bash
# Ensure the virtual environment is activated
pytest --cov=src --cov-report=term-missing
```

All tests should pass:

```
============================= test session starts ==============================
collected 6 items

tests/test_calculator.py ......                                         [100%]

============================== 6 passed in 0.03s ===============================
```

## Design Decisions

- **Type Safety**: The `Calculator` methods perform runtime checks to ensure both arguments are numbers, providing early feedback for incorrect usage.
- **Extensibility**: The class is deliberately lightweight, making it easy to extend with additional operations (multiply, divide, etc.) without breaking existing code.
- **Package Layout**: Placing the core implementation under `src/` mirrors common Python project layouts and keeps the import path clean (`from src.calculator import Calculator`).

## License

This project is licensed under the MIT License – see the `LICENSE` file for details.

---

*Happy coding!*