def solve(f, a, b, n):
    """Approximate the integral of f using the composite trapezoid rule.

    Args:
        f: callable, the function to integrate.
        a: numeric, lower limit of integration.
        b: numeric, upper limit of integration.
        n: int, number of subintervals.

    Returns:
        float: approximation of the integral.

    Raises:
        TypeError: if f is not callable, a or b not numeric, or n not int.
        ValueError: if n <= 0.
    """
    if not callable(f):
        raise TypeError("f must be callable")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise TypeError("a and b must be numeric")
    a = float(a)
    b = float(b)
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n <= 0:
        raise ValueError("n must be > 0")
    if b == a:
        return 0.0
    h = (b - a) / n
    total = f(a) + f(b)
    for i in range(1, n):
        x = a + i * h
        total += 2 * f(x)
    return (h / 2) * total
