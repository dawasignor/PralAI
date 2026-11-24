"""One number format for everything a reader sees (user decision 2026-10-27,
BUG-059): three significant figures, shares as percentages, the unit after
the value. Whole numbers -- counts, people -- stay exact: rounding a count
would invent a figure nobody measured.

    num(22.88)        -> "22.9"
    num(-0.0083563)   -> "-0.00836"
    num(1234.5)       -> "1,230"
    num(69487000)     -> "69,487,000"     (an int: exact)
    share(0.211013)   -> "21.1%"
"""

from __future__ import annotations

import math
from typing import Any

SIG = 3


def num(v: Any, sig: int = SIG) -> str:
    if v is None:
        return "--"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x == 0 or not math.isfinite(x):
        return "0" if x == 0 else str(x)
    digits = sig - 1 - int(math.floor(math.log10(abs(x))))
    r = round(x, digits)
    if digits <= 0:
        return f"{int(r):,}"
    return f"{r:,.{digits}f}".rstrip("0").rstrip(".")


def share(fraction: Any, sig: int = SIG) -> str:
    """A 0-1 fraction as a percentage."""
    return "--" if fraction is None else f"{num(float(fraction) * 100, sig)}%"
