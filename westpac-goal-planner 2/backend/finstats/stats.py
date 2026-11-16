"""Small, dependency-free statistics helpers (robust and classical).

Everything takes plain sequences of numbers so it can be reused anywhere.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# 0.6745 is the 75th percentile of the standard normal; it makes MAD comparable to a std dev.
MODIFIED_Z_CONST = 0.6745
# Fallback scale when MAD is 0 (Iglewicz & Hoaglin): mean absolute deviation * 1.253314.
MEAN_AD_CONST = 1.253314


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def median(xs: Iterable[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def quantile(xs: Iterable[float], q: float) -> float:
    """Linear-interpolated quantile, q in [0, 1]."""
    s = sorted(xs)
    if not s:
        return 0.0
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def std(xs: Sequence[float], ddof: int = 1) -> float:
    """Standard deviation. ddof=1 gives the sample std (default), ddof=0 the population std."""
    n = len(xs)
    if n - ddof <= 0:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def mad(xs: Sequence[float], centre: float | None = None) -> float:
    """Raw median absolute deviation (not rescaled)."""
    if not xs:
        return 0.0
    c = median(xs) if centre is None else centre
    return median(abs(x - c) for x in xs)


def modified_z(x: float, xs: Sequence[float]) -> float:
    """Modified z-score of x against the sample xs (Iglewicz & Hoaglin).

    z = 0.6745 * (x - median) / MAD. If MAD is 0 (e.g. many identical values) it falls back to
    (x - median) / (1.253314 * meanAD). If both scales are 0 it returns +/-inf for any x that
    differs from the median and 0 otherwise.
    """
    if not xs:
        return 0.0
    med = median(xs)
    m = mad(xs, med)
    if m > 0:
        return MODIFIED_Z_CONST * (x - med) / m
    mean_ad = mean([abs(v - med) for v in xs])
    if mean_ad > 0:
        return (x - med) / (MEAN_AD_CONST * mean_ad)
    if x == med:
        return 0.0
    return math.inf if x > med else -math.inf


def rolling_mean(xs: Sequence[float], window: int) -> list[float]:
    """Trailing rolling mean; output[i] = mean(xs[i-window+1 .. i]). Length is len(xs)-window+1."""
    if window <= 0:
        raise ValueError("window must be positive")
    return [sum(xs[i - window + 1:i + 1]) / window for i in range(window - 1, len(xs))]


def ewma(xs: Sequence[float], alpha: float | None = None, span: float | None = None) -> float:
    """Last value of an exponentially weighted moving average (recent values weigh most).

    Give either alpha (0 < alpha <= 1) or span (alpha = 2 / (span + 1)). Seeds with xs[0].
    """
    if not xs:
        return 0.0
    if alpha is None:
        alpha = 2 / ((span if span is not None else 6) + 1)
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    s = xs[0]
    for x in xs[1:]:
        s = alpha * x + (1 - alpha) * s
    return s
