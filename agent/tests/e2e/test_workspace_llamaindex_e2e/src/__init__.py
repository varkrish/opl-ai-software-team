# -*- coding: utf-8 -*-
"""
src package initialization.

Provides a simple, production‑ready :class:`Calculator` for basic arithmetic
operations. The class validates its inputs to ensure only ``int`` or ``float``
values are processed, raising a clear :class:`TypeError` for unsupported
types. This defensive approach keeps the API reliable for downstream code and
unit tests.
"""

from __future__ import annotations

from typing import Union

Number = Union[int, float]


class Calculator:
    """A minimal calculator supporting addition and subtraction.

    The public methods accept two numeric arguments and return the result of
    the operation. Non‑numeric inputs raise :class:`TypeError` with an
    informative message, which is exercised by the test suite.
    """

    @staticmethod
    def _validate_number(value: object) -> Number:
        """Validate that *value* is an ``int`` or ``float``.

        Parameters
        ----------
        value: object
            The value to validate.

        Returns
        -------
        Number
            The original value if it is a valid numeric type.

        Raises
        ------
        TypeError
            If *value* is not an ``int`` or ``float``.
        """
        if isinstance(value, (int, float)):
            return value
        raise TypeError(
            f"Calculator methods expect int or float, got {type(value).__name__}"
        )

    def add(self, a: Number, b: Number) -> Number:
        """Return the sum of *a* and *b*.

        Both arguments are validated to be numeric before the addition is
        performed.
        """
        a_val = self._validate_number(a)
        b_val = self._validate_number(b)
        return a_val + b_val

    def subtract(self, a: Number, b: Number) -> Number:
        """Return the difference of *a* minus *b*.

        Both arguments are validated to be numeric before the subtraction is
        performed.
        """
        a_val = self._validate_number(a)
        b_val = self._validate_number(b)
        return a_val - b_val


__all__ = ["Calculator"]