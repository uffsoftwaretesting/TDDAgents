import numpy as np

def solve(f, a, b, n):
    """
    Approximate the definite integral of f from a to b using the composite trapezoidal rule with n subintervals.

    Parameters:
    - f: callable[[float], float]
    - a: int or float, lower bound
    - b: int or float, upper bound
    - n: int > 0, number of subintervals

    Returns:
    - float: approximation of the integral

    Raises:
    - TypeError: if f is not callable or a/b are not numeric
    - ValueError: if n is not a positive integer
    """
    # Validate inputs
    if not callable(f):
        raise TypeError("f must be callable")
    if not isinstance(a, (int, float)):
        raise TypeError("a must be a number")
    if not isinstance(b, (int, float)):
        raise TypeError("b must be a number")
    if not isinstance(n, int) or n <= 0:
        raise ValueError("n must be a positive integer")

    # Convert to float and compute step
    a = float(a)
    b = float(b)
    h = (b - a) / n

    # Generate mesh points using numpy.linspace
    xs = np.linspace(a, b, n + 1)

    total = 0.0
    for i, x in enumerate(xs):
        xi = float(x)
        if i == 0 or i == n:
            total += f(xi)
        else:
            total += 2 * f(xi)

    return (h / 2) * total
