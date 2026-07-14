class Calculator:
    """Simple calculator providing basic arithmetic operations."""

    def add(self, a: float, b: float) -> float:
        """Return the sum of *a* and *b*.

        Parameters
        ----------
        a: float
            First addend.
        b: float
            Second addend.

        Returns
        -------
        float
            The arithmetic sum of ``a`` and ``b``.
        """
        return a + b

    def subtract(self, a: float, b: float) -> float:
        """Return the difference of *a* minus *b*.

        Parameters
        ----------
        a: float
            Minuend.
        b: float
            Subtrahend.

        Returns
        -------
        float
            The result of ``a - b``.
        """
        return a - b
