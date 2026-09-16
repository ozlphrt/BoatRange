"""PCHIP (Piecewise Cubic Hermite Interpolating Polynomial) interpolation.

Shape-preserving monotonic cubic interpolation — the key algorithm for the
Axopar fuel model. Unlike unrestricted cubic splines, PCHIP guarantees:

1. Monotonicity preservation: if data is increasing, output is increasing.
2. No overshoot: interpolated values stay within [y_min, y_max] between points.
3. Linear regions: if data is linear between two points, interpolation is linear.

This is critical for the fuel curve because:
- Speed vs RPM is monotonically increasing (guaranteed by PCHIP)
- Fuel flow vs RPM is monotonically increasing (guaranteed by PCHIP)
- No artificial dips or peaks that don't exist in measured data

Reference: Fritsch-Carlson method for monotonic cubic interpolation.
Source: Fritsch, F. N., & Carlson, R. E. (1980). "Monotone piecewise
        cubic interpolation." SIAM Journal on Numerical Analysis, 17(2), 238-246.
"""

from __future__ import annotations


def pchip_interpolate(x: list[float], y: list[float], xi: float) -> float:
    """Evaluate PCHIP interpolation at a single point.

    Args:
        x: Sorted x-coordinates of control points (must be strictly increasing).
        y: y-coordinates of control points.
        xi: Point to evaluate.

    Returns:
        Interpolated y value at xi.

    Raises:
        ValueError: If xi is outside the range [x[0], x[-1]].
        ValueError: If x has fewer than 2 points.
    """
    if len(x) < 2:
        raise ValueError("Need at least 2 control points for interpolation")

    if xi < x[0] or xi > x[-1]:
        raise ValueError(
            f"Extrapolation not allowed: xi={xi} is outside "
            f"[{x[0]}, {x[-1]}]"
        )

    # Find the interval [x[i], x[i+1]] containing xi
    i = _find_interval(x, xi)

    # Compute PCHIP slopes at each node
    slopes = _compute_pchip_slopes(x, y)

    # Evaluate Hermite polynomial on this interval
    return _hermite_evaluate(x[i], x[i + 1], y[i], y[i + 1], slopes[i], slopes[i + 1], xi)


def monotonic_cubic_interp(
    x: list[float], y: list[float], x_new: list[float]
) -> list[float]:
    """Evaluate PCHIP interpolation at multiple points.

    Args:
        x: Sorted x-coordinates of control points.
        y: y-coordinates of control points.
        x_new: Points to evaluate (all must be within [x[0], x[-1]]).

    Returns:
        List of interpolated y values, one per point in x_new.

    Raises:
        ValueError: If any x_new is outside the range.
    """
    return [pchip_interpolate(x, y, xi) for xi in x_new]


# ============================================================================
# Internal Implementation — Fritsch-Carlson Method
# ============================================================================


def _find_interval(x: list[float], xi: float) -> int:
    """Find index i such that x[i] <= xi <= x[i+1]."""
    # Binary search for efficiency
    lo, hi = 0, len(x) - 2
    while lo <= hi:
        mid = (lo + hi) // 2
        if x[mid] <= xi <= x[mid + 1]:
            return mid
        elif xi < x[mid]:
            hi = mid - 1
        else:
            lo = mid + 1
    # Handle edge case where xi == x[-1]
    return len(x) - 2


def _compute_pchip_slopes(x: list[float], y: list[float]) -> list[float]:
    """Compute PCHIP slopes at each node using the Fritsch-Carlson method.

    For each interior point i, compute the secant slopes:
        d[i] = (y[i+1] - y[i]) / (x[i+1] - x[i])

    Then the slope m[i] at each node is:
        - 0 if adjacent secants have opposite signs (local extremum)
        - weighted HARMONIC mean of adjacent secants otherwise:
              m[i] = (w1 + w2) / (w1/d[i-1] + w2/d[i])
          with weights w1 = 2*h[i] + h[i-1], w2 = h[i] + 2*h[i-1]
          (h[k] = x[k+1] - x[k]).

    This ensures monotonicity preservation and matches scipy's
    PchipInterpolator to within floating-point tolerance.

    Regression note: an earlier version of this function used a weighted
    ARITHMETIC mean here (``(w1*d[i-1] + w2*d[i]) / (w1+w2)``) with the wrong
    weights on top of that (``3*h[i] + h[i-1]`` instead of ``2*h[i] +
    h[i-1]``). The arithmetic mean is not guaranteed to keep the resulting
    Hermite segment monotonic the way the *harmonic* mean is — on a curve
    with a sharp change in secant slope between two segments, it produced
    interpolated values well outside the [y_min, y_max] range of the
    surrounding data points (verified: a synthetic 4-point curve with a slope
    change from 0.1/unit to 9.8/unit overshot to -0.45 partway through the
    slower segment, where the true monotone curve never goes below 0.1). That
    directly contradicted this module's own "no overshoot" guarantee and
    CLAUDE.md Section 4.3's explicit ban on unrestricted-spline overshoot.

    Reference: Fritsch & Carlson (1980), Section 3 (equation 2 for m_i and
    the sign test in equation 5); this is also exactly the formula scipy's
    ``PchipInterpolator`` implements.
    """
    n = len(x)
    # Compute secant slopes between consecutive points
    d: list[float] = []
    h: list[float] = []
    for i in range(n - 1):
        dx = x[i + 1] - x[i]
        dy = y[i + 1] - y[i]
        if abs(dx) < 1e-15:
            raise ValueError("x values must be strictly increasing")
        h.append(dx)
        d.append(dy / dx)

    # Compute slopes at each node
    m: list[float] = [0.0] * n

    # First and last point: non-centered three-point one-sided derivative
    # estimate (Fritsch-Carlson / de Boor), the same formula scipy's
    # PchipInterpolator uses — a plain "endpoint slope = last secant" is
    # monotonic but a visibly worse fit right at the ends of the curve
    # whenever the two edge segments have very different steepness (exactly
    # the Axopar curve's shape: shallow near idle, steep near WOT).
    m[0] = _endpoint_slope(h[0], h[1], d[0], d[1]) if n > 2 else d[0]
    m[-1] = _endpoint_slope(h[-1], h[-2], d[-1], d[-2]) if n > 2 else d[-1]

    # Interior points
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            # Opposite signs (or a flat segment) → local extremum → slope is zero
            m[i] = 0.0
        else:
            # Weighted harmonic mean (Fritsch-Carlson eq. 2)
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    # Ensure monotonicity: if secant and node slope have opposite signs,
    # set slope to zero
    for i in range(n):
        if 0 < i < n - 1:
            if d[i - 1] * m[i] < 0 or d[i] * m[i] < 0:
                m[i] = 0.0

    return m


def _endpoint_slope(h0: float, h1: float, d0: float, d1: float) -> float:
    """One-sided derivative estimate at a curve endpoint.

    ``h0``/``d0`` are the step/secant slope of the segment touching the
    endpoint; ``h1``/``d1`` are the next segment inward.

    m = ((2*h0 + h1)*d0 - h0*d1) / (h0 + h1)

    then clamped so the endpoint segment stays monotone and doesn't
    overshoot: zeroed if it disagrees in sign with ``d0``, capped at
    ``3*d0`` if ``d0`` and ``d1`` disagree in sign (a corner in the data
    right at the edge).
    """
    m = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
    if m * d0 <= 0:
        return 0.0
    if d0 * d1 <= 0 and abs(m) > 3 * abs(d0):
        return 3 * d0
    return m


def _hermite_evaluate(
    x0: float, x1: float, y0: float, y1: float,
    m0: float, m1: float, xi: float
) -> float:
    """Evaluate the cubic Hermite polynomial on [x0, x1].

    Uses the standard Hermite basis functions:

        h00(t) = (1 + 2t)(1 - t)^2
        h10(t) = t(1 - t)^2
        h01(t) = t^2(3 - 2t)
        h11(t) = t^2(t - 1)

    where t = (xi - x0) / (x1 - x0).

    y(xi) = h00(t)*y0 + h10(t)*(x1-x0)*m0 + h01(t)*y1 + h11(t)*(x1-x0)*m1
    """
    if abs(x1 - x0) < 1e-15:
        return y0

    t = (xi - x0) / (x1 - x0)

    # Hermite basis functions
    t2 = t * t
    t3 = t2 * t

    h00 = (1 + 2 * t) * (1 - t) * (1 - t)
    h10 = t * (1 - t) * (1 - t)
    h01 = t2 * (3 - 2 * t)
    h11 = t2 * (t - 1)

    dx = x1 - x0

    result = (h00 * y0 + h10 * dx * m0 +
              h01 * y1 + h11 * dx * m1)

    return result
