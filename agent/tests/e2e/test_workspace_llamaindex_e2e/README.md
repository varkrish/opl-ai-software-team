# Simple Python Calculator

This project provides a minimal `Calculator` class with basic arithmetic operations and a test suite using **pytest**.

## Project Structure
```
project/
├── calculator.py          # Implementation of Calculator class
├── requirements.txt       # Project dependencies
├── README.md              # This file
└── tests/
    ├── __init__.py        # Makes `tests` a package
    └── test_calculator.py # Unit tests for Calculator
```

## Setup
1. Ensure you have Python 3.11 installed.
2. (Optional) Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows use `venv\Scripts\activate`
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage
You can use the `Calculator` class directly in your Python code:
```python
from calculator import Calculator

calc = Calculator()
print(calc.add(2, 3))        # -> 5
print(calc.subtract(5, 2))   # -> 3
```

## Running Tests
Execute the test suite with:
```bash
pytest -v
```
All tests should pass, confirming the correctness of the `add` and `subtract` methods for positive, negative, and zero values.
