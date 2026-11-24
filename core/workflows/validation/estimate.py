"""Point labels and design-based estimates from the labelled sample.

Design: docs/sample_reference.md. Two parts, both pure:

1. **The point label is computed, never typed.** `point_label` applies the
   cumulative rule to one point's observations (one row per image in
   `labels.csv`): `flood` if any in-window observation shows flood;
   `not_flood` / `preexisting_water` only if a clear observation on or after
   the peak day shows it and none shows flood; anything else `unresolved` --
   excluded and counted, never guessed. A product is scored only on labels
   from images its own sensors did not take (`independent_of`).

2. **Stehman (2014) estimators for stratified random sampling when the strata
   are not one map's classes.** Every quantity here is a total or a ratio of
   two totals over the domain:

       Y_hat = sum_h N_h * ybar_h
       R_hat = Y_hat / X_hat
       V(Y_hat) = sum_h N_h^2 (1 - n_h/N_h) s2_yh / n_h
       V(R_hat) = 1/X_hat^2 sum_h N_h^2 (1 - n_h/N_h)
                  (s2_yh + R^2 s2_xh - 2 R s_xyh) / n_h

   with N_h the stratum's cell count and n_h its RESOLVED points. Precision,
   recall, IoU and F1 are all ratios of indicator totals (see `RATIOS`), so one
   estimator serves them all and each gets a standard error. Unresolved points
   are dropped within their stratum, which assumes they are missing at random
   there; the report says so.

A stratum with fewer than 2 resolved points has no variance estimate: its
standard error is None and so is every interval it enters. Nothing is filled.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import numpy as np

CLEAR = ("not_flood", "preexisting_water")
Z95 = 1.959963984540054

# Sensor of each label source, to test independence against a product's
# `platforms` in the config.
SOURCE_PLATFORM = {"sentinel2_l2a": "sentinel-2", "landsat_c2_l2": "landsat",
                   "vhr": "vhr"}


def _local_date(stamp: str, utc_offset: timedelta) -> date:
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (when + utc_offset).date()


def point_label(observations: list[dict[str, str]], peak: date,
                utc_offset: timedelta = timedelta(0)) -> dict[str, Any]:
    """The cumulative rule over one point's observations (rows of labels.csv)."""
    flood = [o for o in observations if o["class"] == "flood"]
    if flood:
        return {"label": "flood", "basis": [o["image_id"] for o in flood]}
    after = [o for o in observations if o["class"] in CLEAR
             and _local_date(o["image_datetime_utc"], utc_offset) >= peak]
    if after:
        cls = "preexisting_water" if any(o["class"] == "preexisting_water" for o in after) \
            else "not_flood"
        return {"label": cls, "basis": [o["image_id"] for o in after]}
    if any(o["class"] in CLEAR for o in observations):
        reason = "clear only before the peak day"
    elif observations:
        reason = "no clear observation (" + ", ".join(sorted(
            {o.get("unobservable_reason") or o["class"] for o in observations})) + ")"
    else:
        reason = "no independent observation"
    return {"label": "unresolved", "reason": reason, "basis": []}


def independent_of(observations: list[dict[str, str]],
                   platforms: Iterable[str]) -> list[dict[str, str]]:
    """The observations whose image a product with `platforms` did not use."""
    used = {p.lower() for p in platforms}
    return [o for o in observations
            if SOURCE_PLATFORM.get(o["source"], o["source"]) not in used]


# -- the estimator ----------------------------------------------------------
def _stratum_moments(y: np.ndarray, x: np.ndarray | None) -> dict[str, float | None]:
    n = len(y)
    out = {"n": n, "ybar": float(y.mean()) if n else None,
           "xbar": float(x.mean()) if (x is not None and n) else None,
           "s2y": None, "s2x": None, "sxy": None}
    if n >= 2:
        out["s2y"] = float(y.var(ddof=1))
        if x is not None:
            out["s2x"] = float(x.var(ddof=1))
            out["sxy"] = float(np.cov(y, x, ddof=1)[0, 1])
    return out


def total(strata: dict[str, dict[str, Any]], values: dict[str, np.ndarray]) -> dict[str, Any]:
    """Stratified estimate of a domain total of `values` (per-point y).

    `strata[h]` gives `N` (cells; or any area unit, consistently). `values[h]`
    holds y for the resolved points of h. A stratum with no resolved point
    leaves the total undefined: its share of the domain is unknown."""
    est, var, missing = 0.0, 0.0, []
    for h, info in strata.items():
        N = float(info["N"])
        if N == 0:
            continue
        y = np.asarray(values.get(h, []), dtype=float)
        m = _stratum_moments(y, None)
        if m["n"] == 0:
            missing.append(h)
            continue
        est += N * m["ybar"]
        if m["s2y"] is None or var is None:
            var = None
        else:
            var += N * N * (1 - m["n"] / N) * m["s2y"] / m["n"]
    if missing:
        return {"estimate": None, "se": None, "ci95": None,
                "undefined": f"no resolved point in {', '.join(missing)}"}
    se = None if var is None else var ** 0.5
    return {"estimate": est, "se": se,
            "ci95": None if se is None else [est - Z95 * se, est + Z95 * se]}


def ratio(strata: dict[str, dict[str, Any]], y: dict[str, np.ndarray],
          x: dict[str, np.ndarray]) -> dict[str, Any]:
    """Stehman's combined ratio estimator R = Y/X, with its standard error."""
    Y = X = 0.0
    moments = {}
    missing = []
    for h, info in strata.items():
        N = float(info["N"])
        if N == 0:
            continue
        yy = np.asarray(y.get(h, []), dtype=float)
        xx = np.asarray(x.get(h, []), dtype=float)
        m = _stratum_moments(yy, xx)
        if m["n"] == 0:
            missing.append(h)
            continue
        moments[h] = (N, m)
        Y += N * m["ybar"]
        X += N * m["xbar"]
    if missing:
        return {"estimate": None, "se": None, "ci95": None,
                "undefined": f"no resolved point in {', '.join(missing)}"}
    if X == 0:
        return {"estimate": None, "se": None, "ci95": None,
                "undefined": "denominator estimated as 0"}
    R = Y / X
    var: float | None = 0.0
    for N, m in moments.values():
        if m["s2y"] is None:
            var = None
            break
        var += N * N * (1 - m["n"] / N) * (m["s2y"] + R * R * m["s2x"]
                                           - 2 * R * m["sxy"]) / m["n"]
    if var is not None:
        var = max(var, 0.0) / (X * X)
    se = None if var is None else var ** 0.5
    return {"estimate": R, "se": se,
            "ci95": None if se is None else [R - Z95 * se, R + Z95 * se],
            "numerator_total": Y, "denominator_total": X}


# Each accuracy measure as (numerator, denominator) indicators of
# r = labelled flood, m = map says flood, c = the map observed the point.
RATIOS = {
    "precision": (lambda r, m, c: r * m * c, lambda r, m, c: m * c),
    "recall": (lambda r, m, c: r * m * c, lambda r, m, c: r * c),
    "iou": (lambda r, m, c: r * m * c, lambda r, m, c: np.maximum(r, m) * c),
    "f1": (lambda r, m, c: 2 * r * m * c, lambda r, m, c: (r + m) * c),
}


def accuracy(strata: dict[str, dict[str, Any]],
             points: dict[str, list[tuple[int, int, int]]]) -> dict[str, Any]:
    """Every measure in RATIOS, plus coverage, for one map.

    `points[h]` is a list of (r, m, c) for the resolved points of stratum h."""
    arrays = {h: np.asarray(v, dtype=float).reshape(-1, 3) for h, v in points.items()}
    out: dict[str, Any] = {}
    for name, (num, den) in RATIOS.items():
        y = {h: num(a[:, 0], a[:, 1], a[:, 2]) for h, a in arrays.items()}
        x = {h: den(a[:, 0], a[:, 1], a[:, 2]) for h, a in arrays.items()}
        out[name] = ratio(strata, y, x)
    ones = {h: np.ones(len(a)) for h, a in arrays.items()}
    out["coverage"] = ratio(strata, {h: a[:, 2] for h, a in arrays.items()}, ones)
    # Points that actually inform each measure -- a precision resting on three
    # mapped-flood points is shown as such, not as a number like any other.
    out["usable_points"] = {
        "observed": int(sum(a[:, 2].sum() for a in arrays.values())),
        "map_flood": int(sum((a[:, 1] * a[:, 2]).sum() for a in arrays.values())),
        "labelled_flood": int(sum((a[:, 0] * a[:, 2]).sum() for a in arrays.values())),
    }
    return out


def group(rows: Iterable[dict[str, str]], key: str = "point_id") -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return dict(out)
