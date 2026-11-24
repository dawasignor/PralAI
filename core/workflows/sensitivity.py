"""How far a criterion weight can move before the ranking stops holding.

`rank_flood_products` produces one order from one set of weights. This module
asks the question a reader of that order should ask next: **how much of the
answer is the evidence, and how much is the weighting?** It perturbs one
criterion weight at a time, re-runs TOPSIS over the same matrix, and reports
the smallest change that alters the order.

**It perturbs weights only.** Not the product scores, not the contested
evidence cells, not the unresolved P2 revisit range, not the author
parameters. Those are separate questions and this output does not answer them
-- a reader who wants to know whether the contested L3 cells are carrying the
ranking will not find it here.

## What "moving a weight" means

The sweep moves criterion *j*'s normalised weight to *t* and rescales the other
eleven by `(1 - t) / (1 - w_j)`, so they keep their relative proportions and
the twelve still sum to 1. That is the standard one-at-a-time construction,
and in this pipeline it has an exact interpretation: it is what stating ONE
weight to the ranking does (`rank_flood_products(weights={j: s})` keeps the
others in proportion and renormalises), so every threshold comes with the
weight to type, `equivalent_stated_weight` = t (1 - w_j) / (1 - t).

**The sweep starts from the weights Module 7 ranked with** (user decision
2026-11-22): the event-first weights, and any weight or score the user
changed at Module 6 or 7, read from the stored ranking. Under the event-first
weighting no criterion is ever zero, and any weight can be stated, so every
threshold is reachable. (Under the old multiplicative rule a zero modulator
made a criterion unreachable -- W2 over Kelani.)

## What a threshold is, and how it is found

Closeness is not linear in a weight -- the distances are Euclidean, so the
response curves bend -- and there is no closed form for the crossing. So each
threshold is bracketed on a grid and then bisected to `tolerance` in weight
units. The predicate is the whole rank vector, not one pair: any change of
order counts, which is the conservative reading.

A sweep runs to both ends. `t = 1` is a degenerate decision on one criterion
and `t = 0` deletes the criterion; both are reported rather than trimmed,
because a criterion that cannot change the order **anywhere** in [0, 1] is the
strongest stability result available and it only exists if the ends are
searched.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from . import workflow
from .mcdm import rank_flood_products, topsis


# ---------------------------------------------------------------------------
# Rank correlation, tie-safe
# ---------------------------------------------------------------------------
#
# Written out rather than taken from scipy: scipy is not a dependency of this
# repo and a rank correlation over five alternatives is not worth becoming one.
# Both handle ties, because TOPSIS ties exactly at a crossing and a formula
# that assumes distinct ranks returns a number that is quietly wrong there --
# at precisely the weights this analysis is about.
def midranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged. Higher value = better = lower rank number."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while (j + 1 < len(order)
               and values[order[j + 1]] == values[order[i]]):
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def spearman(a: list[float], b: list[float]) -> float:
    """Pearson correlation of the midranks: Spearman's rho, tie-safe."""
    ra, rb = midranks(a), midranks(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra)
                    * sum((y - mb) ** 2 for y in rb))
    return float(num / den) if den > 0 else float("nan")


def kendall_tau_b(a: list[float], b: list[float]) -> float:
    """Kendall's tau-b: concordance with the tie correction on both sides.

        tau_b = (C - D) / sqrt((n0 - n1) * (n0 - n2))

    where n0 is every pair, n1 the pairs tied in `a` and n2 the pairs tied in
    `b`. A pair tied in BOTH belongs to n1 and to n2, so it drops out of each
    factor rather than being subtracted twice -- which is why the counters
    below separate "tied in a only" from "tied in both". Counting it on both
    sides makes tau of a tied vector with itself come out at 0.833 instead of
    1, and the error only shows up where there are ties: exactly at a
    crossing, which is what this analysis is about.
    """
    n = len(a)
    concordant = discordant = tied_a_only = tied_b_only = 0
    for i in range(n):
        for j in range(i + 1, n):
            da, db = a[i] - a[j], b[i] - b[j]
            if da == 0 and db == 0:
                continue                      # in n1 and n2; cancels in both
            elif da == 0:
                tied_a_only += 1
            elif db == 0:
                tied_b_only += 1
            elif (da > 0) == (db > 0):
                concordant += 1
            else:
                discordant += 1
    pairs = concordant + discordant
    # n0 - n1 = C + D + (tied in b only), and symmetrically for the other.
    den = math.sqrt((pairs + tied_b_only) * (pairs + tied_a_only))
    return float((concordant - discordant) / den) if den > 0 else float("nan")


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
def perturbed_weights(weights: list[float], index: int, t: float) -> np.ndarray:
    """Criterion `index` set to `t`; the others rescaled proportionally."""
    rest = 1.0 - weights[index]
    if rest <= 0.0:
        raise ValueError(
            f"criterion {index} already holds the entire weight, so there is "
            "nothing left to rescale. A one-at-a-time sweep is undefined "
            "against a degenerate weight vector.")
    scale = (1.0 - t) / rest
    return np.array([t if i == index else w * scale
                     for i, w in enumerate(weights)], dtype=float)


def _state(matrix: np.ndarray, weights: np.ndarray,
           directions: np.ndarray) -> tuple[tuple[int, ...], list[float]]:
    """(rank vector, closeness) at one weight vector."""
    result = topsis(matrix, weights, directions)
    return (tuple(int(r) for r in result["ranks"]),
            [float(c) for c in result["closeness"]])


def _first_change(matrix: np.ndarray, directions: np.ndarray,
                  weights: list[float], index: int, *,
                  start: float, stop: float, step: float, tolerance: float,
                  changed: Callable[[tuple[int, ...]], bool],
                  ) -> dict[str, Any] | None:
    """Walk `start` -> `stop` and refine the first weight where `changed`.

    The grid brackets the crossing and bisection refines it; the predicate is
    evaluated on the rank vector, so "the order changed" and "the leader
    changed" share one search. Returns None when the predicate never trips,
    which is a result and not a failure: that criterion cannot reorder the
    ranking anywhere in [0, 1].
    """
    steps = max(1, int(round(abs(stop - start) / step)))
    direction = 1.0 if stop > start else -1.0
    previous = start

    def _reordered(ranks: tuple[int, ...]) -> bool:
        # A weight vector that discriminates nothing -- every alternative on
        # rank 1 -- is not a different ranking, it is the absence of one. It
        # happens at t = 1 on a criterion where the candidates are identical,
        # and reporting "the order changes at 1.0" there would put a
        # threshold on the collapse of the decision problem rather than on a
        # swap. A partial tie still counts: two alternatives that were 2 and
        # 3 and are now both 2 have genuinely stopped holding their order.
        if len(set(ranks)) == 1:
            return False
        return changed(ranks)

    for i in range(1, steps + 1):
        t = stop if i == steps else min(1.0, max(0.0, start + direction * step * i))
        ranks, _ = _state(matrix, perturbed_weights(weights, index, t),
                          directions)
        if _reordered(ranks):
            lo, hi = previous, t
            for _ in range(200):
                if abs(hi - lo) <= tolerance:
                    break
                mid = (lo + hi) / 2.0
                mid_ranks, _ = _state(
                    matrix, perturbed_weights(weights, index, mid), directions)
                if _reordered(mid_ranks):
                    hi = mid
                else:
                    lo = mid
            ranks_at, closeness_at = _state(
                matrix, perturbed_weights(weights, index, hi), directions)
            return {"weight": float(hi), "ranks": list(ranks_at),
                    "closeness": closeness_at}
        previous = t
    return None


def _equivalent_stated_weight(target: float, current: float) -> float | None:
    """The weight to STATE for this criterion so the ranking puts it at
    `target`, the others kept as they are and renormalised:

        target = s / (s + 1 - w)   =>   s = target (1 - w) / (1 - target)

    None for a target of exactly 1, which asks for every other criterion to be
    deleted rather than for this one to be raised.
    """
    if target >= 1.0:
        return None
    if target <= 0.0:
        return 0.0
    return float(target * (1.0 - current) / (1.0 - target))


def _sweep(
    cloud_pct: float,
    wind_ms: float,
    veg_pct: float,
    steep_pct: float,
    urban_pct: float,
    mode: str = "WEIGHT",
    base_weights: dict[str, float] | list[float] | None = None,
    parameters: dict[str, float] | None = None,
    grid_step: float = 0.005,
    report_step: float = 0.1,
    tolerance: float = 1e-9,
    weights: dict[str, float] | None = None,
    scores: dict[str, Any] | None = None,
    weighting: str = "event_first",
) -> dict[str, Any]:
    """One-at-a-time weight sensitivity for one event's product ranking.

    Takes the same five indices as `rank_flood_products` and re-runs it to get
    the baseline, rather than accepting a ranking payload: the baseline must be
    the ranking this analysis claims to be about, and a payload passed in by
    hand can be from another event, another parameter set, or an older run.
    Passing the indices makes that impossible to get wrong, and the run is
    free -- nothing here fetches anything.

    Args:
        cloud_pct: AOI cloud cover for the event, 0-100.
        wind_ms: AOI wind speed for the event, m/s, >= 0.
        veg_pct: share of the AOI in the dense-vegetation class, 0-100.
        steep_pct: share classified steep or very steep, 0-100.
        urban_pct: share with BUI above the built-up threshold, 0-100.
        mode: passed through to `rank_flood_products`.
        base_weights: passed through to `rank_flood_products`.
        parameters: passed through to `rank_flood_products`.
        grid_step: spacing of the bracketing grid, in normalised weight units.
            Smaller finds narrower windows of instability; a crossing narrower
            than one step between two identical orders is invisible to any
            grid, which is why `grid_step` is reported in the output.
        report_step: spacing of the printed response curves. Purely a
            reporting choice -- no threshold is computed from it.
        tolerance: bisection tolerance for a threshold, in weight units.

    Returns the baseline ranking, a per-criterion threshold table, the leader
    changes, rank agreement across each sweep, and the response curves.
    """
    if not 0.0 < grid_step <= 0.25:
        raise ValueError(f"grid_step must be in (0, 0.25], got {grid_step}")
    if not 0.0 < report_step <= 0.5:
        raise ValueError(f"report_step must be in (0, 0.5], got {report_step}")
    if tolerance <= 0.0:
        raise ValueError(f"tolerance must be > 0, got {tolerance}")

    base = rank_flood_products(
        cloud_pct=cloud_pct, wind_ms=wind_ms, veg_pct=veg_pct,
        steep_pct=steep_pct, urban_pct=urban_pct, mode=mode,
        base_weights=base_weights, parameters=parameters, use_module6=False,
        weights=weights, scores=scores, weighting=weighting)

    criteria = base["topsis"]["criterion_order"]
    names = base["topsis"]["row_order"]
    by_name = {a["name"]: a for a in base["alternatives"]}
    ids = [by_name[n]["id"] for n in names]

    values = base["decision_matrix"]["values"]
    directions = np.array([base["decision_matrix"]["directions"][c]
                           for c in criteria], dtype=int)
    matrix = np.array([[values[a][c] for c in criteria] for a in ids],
                      dtype=float)
    weights = [next(c["weight"] for c in base["criteria"] if c["id"] == cid)
               for cid in criteria]
    meta = {c["id"]: c for c in base["criteria"]}

    baseline_ranks, baseline_closeness = _state(
        matrix, np.array(weights, dtype=float), directions)

    # The baseline must be the ranking in output 4, not merely one computed
    # the same way. A drift here means the matrix or the weights were read out
    # of the payload wrongly, and every threshold below would be measured from
    # the wrong place.
    stated = {r["alternative"]: r["closeness"] for r in base["ranking"]}
    drift = max(abs(stated[n] - c)
                for n, c in zip(names, baseline_closeness))
    if drift > 1e-9:
        raise ValueError(
            f"the recomputed baseline does not reproduce the ranking it came "
            f"from (max closeness drift {drift:.3e}). The sweep is measured "
            "from the baseline, so a mismatch invalidates every threshold.")

    leader = names[baseline_ranks.index(1)]
    baseline_order = [names[i] for i in
                      sorted(range(len(names)), key=lambda i: baseline_ranks[i])]

    curve_grid = [round(k * report_step, 12)
                  for k in range(int(round(1.0 / report_step)) + 1)]
    agreement_points = int(math.ceil(1.0 / grid_step))

    per_criterion: list[dict[str, Any]] = []
    for index, cid in enumerate(criteria):
        info = meta[cid]
        w0 = weights[index]
        modulator = float(info["modulator"])
        reachable = True                 # any weight can be stated (module docstring)

        searches = {}
        for label, stop in (("up", 1.0), ("down", 0.0)):
            searches[label] = _first_change(
                matrix, directions, weights, index,
                start=w0, stop=stop, step=grid_step, tolerance=tolerance,
                changed=lambda ranks: ranks != baseline_ranks)

        leader_searches = {}
        for label, stop in (("up", 1.0), ("down", 0.0)):
            leader_searches[label] = _first_change(
                matrix, directions, weights, index,
                start=w0, stop=stop, step=grid_step, tolerance=tolerance,
                changed=lambda ranks: ranks[baseline_ranks.index(1)] != 1)

        def _pack(found: dict[str, Any] | None) -> dict[str, Any] | None:
            if found is None:
                return None
            order = [names[i] for i in
                     sorted(range(len(names)), key=lambda i: found["ranks"][i])]
            moved = [{"alternative": names[i],
                      "from": baseline_ranks[i], "to": found["ranks"][i]}
                     for i in range(len(names))
                     if found["ranks"][i] != baseline_ranks[i]]
            return {
                "weight": round(found["weight"], 12),
                "delta": round(found["weight"] - w0, 12),
                "relative_delta": (round((found["weight"] - w0) / w0, 12)
                                   if w0 > 0 else None),
                "equivalent_stated_weight": _equivalent_stated_weight(found["weight"], w0),
                "order": order,
                "leader": order[0],
                "moved": moved,
                "closeness": {n: round(c, 12)
                              for n, c in zip(names, found["closeness"])},
            }

        packed = {k: _pack(v) for k, v in searches.items()}
        packed_leader = {k: _pack(v) for k, v in leader_searches.items()}

        candidates = [v for v in packed.values() if v is not None]
        nearest = min(candidates, key=lambda v: abs(v["delta"])) if candidates else None
        leader_candidates = [v for v in packed_leader.values() if v is not None]
        nearest_leader = (min(leader_candidates, key=lambda v: abs(v["delta"]))
                          if leader_candidates else None)

        # The printed curve is coarse because a table is read by a person;
        # the agreement statistics are measured on the fine grid, because
        # "how much of the weight space keeps this order" is a claim about
        # the space and not about eleven chosen points in it.
        curve = []
        for t in curve_grid:
            ranks, closeness = _state(
                matrix, perturbed_weights(weights, index, t), directions)
            curve.append({
                "weight": t,
                "closeness": {n: round(c, 12) for n, c in zip(names, closeness)},
                "order": [names[i] for i in
                          sorted(range(len(names)), key=lambda i: ranks[i])],
                "spearman": round(spearman(closeness, baseline_closeness), 12),
                "kendall_tau_b": round(
                    kendall_tau_b(closeness, baseline_closeness), 12),
            })

        agreement_rho, agreement_tau = [], []
        same_order = same_leader = 0
        for k in range(agreement_points + 1):
            t = min(1.0, k * grid_step)
            ranks, closeness = _state(
                matrix, perturbed_weights(weights, index, t), directions)
            agreement_rho.append(spearman(closeness, baseline_closeness))
            agreement_tau.append(kendall_tau_b(closeness, baseline_closeness))
            same_order += int(ranks == baseline_ranks)
            same_leader += int(ranks[baseline_ranks.index(1)] == 1)

        per_criterion.append({
            "criterion": cid,
            "name": info["name"],
            "direction": "benefit" if info["direction"] == 1 else "cost",
            "base_weight": info["base_weight"],
            "modulated_by": info["modulated_by"],
            "modulator": modulator,
            "weight": round(w0, 12),
            "reachable": reachable,
            "reachability_note": None,
            "first_change": packed,
            "nearest_change": nearest,
            "stable_everywhere": nearest is None,
            "leader_change": packed_leader,
            "nearest_leader_change": nearest_leader,
            "agreement": {
                "min_spearman": round(min(agreement_rho), 12),
                "min_kendall_tau_b": round(min(agreement_tau), 12),
                "mean_spearman": round(sum(agreement_rho) / len(agreement_rho), 12),
                "grid_points": len(agreement_rho),
                "order_unchanged_fraction": round(same_order / len(agreement_rho), 12),
                "leader_unchanged_fraction": round(same_leader / len(agreement_rho), 12),
            },
            "curve": curve,
        })

    ranked_changes = sorted(
        (c for c in per_criterion if c["nearest_change"] is not None),
        key=lambda c: abs(c["nearest_change"]["delta"]))
    reachable_changes = [c for c in ranked_changes if c["reachable"]]

    def _critical(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not pool:
            return None
        first = pool[0]
        return {
            "criterion": first["criterion"],
            "name": first["name"],
            "weight": first["weight"],
            "threshold_weight": first["nearest_change"]["weight"],
            "delta": first["nearest_change"]["delta"],
            "relative_delta": first["nearest_change"]["relative_delta"],
            "moved": first["nearest_change"]["moved"],
            "order_after": first["nearest_change"]["order"],
        }

    return {
        "method": {
            "name": "one-at-a-time weight sensitivity over TOPSIS",
            "base_method": base["method"]["name"],
            "mode": base["method"]["mode"],
            "normalisation": base["method"]["normalisation"],
            "rank_rule": base["method"]["rank_rule"],
            "construction": (
                "criterion j's normalised weight is set to t and the other "
                "eleven are rescaled by (1-t)/(1-w_j), preserving their "
                "proportions -- exactly what stating weight j to the ranking "
                "does; the weight to state is equivalent_stated_weight."),
            "weighting": base["method"].get("weighting"),
            "search": (
                f"bracketed on a grid of {grid_step} in normalised weight "
                f"units, then bisected to {tolerance:g}. The predicate is the "
                "whole rank vector: any change of order counts."),
            "perturbs": "criterion weights only",
            "does_not_perturb": [
                "product scores in the decision matrix",
                "the contested evidence cells",
                "the unresolved P2 revisit range",
                "the author parameters (cloud curve, wind bounds, gate)",
                "the five event indices themselves",
            ],
            "wsm_implemented": base["method"]["wsm_implemented"],
            "wsm_note": base["method"]["wsm_note"],
        },
        "grid": {"grid_step": grid_step, "report_step": report_step,
                 "tolerance": tolerance, "curve_grid": curve_grid},
        "indices": base["indices"],
        # Module 8 sweeps around the weights Module 7 ranked with (2026-11-22).
        "weights_source": base["weights_source"],
        "weights_changed": base["weights_changed"],
        "scores_source": base["scores_source"],
        "scores_changed": base["scores_changed"],
        "baseline": {
            "order": baseline_order,
            "leader": leader,
            "ranking": base["ranking"],
            "closeness": {n: round(c, 12)
                          for n, c in zip(names, baseline_closeness)},
            "row_order": names,
            "criterion_order": criteria,
            "weights": {c: round(w, 12) for c, w in zip(criteria, weights)},
            "closest_pair": _closest_pair(base["ranking"]),
        },
        "criteria": per_criterion,
        "most_critical": _critical(ranked_changes),
        "most_critical_reachable": _critical(reachable_changes),
        "stable_criteria": [c["criterion"] for c in per_criterion
                            if c["stable_everywhere"]],
        "leader_changeable_by": [c["criterion"] for c in per_criterion
                                 if c["nearest_leader_change"] is not None],
        "spec": {
            **base["spec"],
            "sensitivity_note": (
                "The specification names no sensitivity procedure -- steps 8-9 "
                "are a weighted-sum robustness check and a Spearman comparison "
                "between methods, which are a different question and are not "
                "implemented. The construction here is the author's."),
        },
    }


def _closest_pair(ranking: list[dict[str, Any]]) -> dict[str, Any]:
    """The adjacent pair with the smallest gap in closeness.

    Reported because it is where a sensitivity analysis is most likely to find
    something, and a reader who knows the gap can judge a threshold without
    re-deriving it.
    """
    ordered = sorted(ranking, key=lambda r: r["rank"])
    gaps = [(abs(a["closeness"] - b["closeness"]), a, b)
            for a, b in zip(ordered, ordered[1:])]
    gap, upper, lower = min(gaps, key=lambda g: g[0])
    return {"upper": upper["alternative"], "lower": lower["alternative"],
            "ranks": [upper["rank"], lower["rank"]], "gap": round(gap, 12)}


def _ranked_choices() -> dict[str, Any]:
    """The weights and scores the stored Module 7 ranking used (the user's,
    whether stated at Module 6 or 7); {} when none were changed."""
    try:
        from .. import experiment, workflows

        store = experiment.current()
        record = (store.cached_input(workflows.cache_name("rank_flood_products"))
                  if store is not None else None)
    except Exception:                                          # noqa: BLE001
        return {}
    kept = ((record or {}).get("payload") or {}).get("user_choices") or {}
    return {"weights": kept.get("weights") or None, "scores": kept.get("scores") or None}


@workflow(
    "sensitivity_analysis",
    "One-at-a-time weight sensitivity for the NRT flood-product ranking, over "
    "the same five event indices rank_flood_products takes. Sweeps each "
    "criterion weight across 0-1 with the others held in proportion, and "
    "returns the smallest weight change that alters the order, the weight at "
    "which the top-ranked product changes, rank agreement across each sweep, "
    "and the closeness response curves. It sweeps around the weights and scores "
    "the Module 7 ranking used, including any the user changed. Weights only: "
    "scores, evidence and parameters are not perturbed. Draws the weight-stability chart (one band per "
    "criterion) and returns it as the run's figure.",
    label="MCDM weight sensitivity",
    source="MCDM template v3",
    data_source_uncertainty=(
        "Everything in the ranking's uncertainty carries over; only the weights are "
        "moved."),
    method_uncertainty=(
        "Each weight is moved one at a time on a grid, so a joint change or a "
        "crossing narrower than grid_step goes unseen. A criterion whose multiplier "
        "is zero cannot be reached at all; SMAA would cover joint uncertainty."),
)
def sensitivity_analysis(
    cloud_pct: float,
    wind_ms: float,
    veg_pct: float,
    steep_pct: float,
    urban_pct: float,
    mode: str = "WEIGHT",
    base_weights: dict[str, float] | list[float] | None = None,
    parameters: dict[str, float] | None = None,
    grid_step: float = 0.005,
    report_step: float = 0.1,
    tolerance: float = 1e-9,
    output_dir: str = "outputs",
    event_id: str = "",
    write_figure: bool = True,
    weights: dict[str, float] | None = None,
    scores: dict[str, Any] | None = None,
    use_module7: bool = True,
    weighting: str = "event_first",
) -> dict[str, Any]:
    """The sweep (`_sweep`), plus its weight-stability chart.

    The chart is `core/charts.stability_bands`, drawn from the payload just
    computed -- every edge read, none recomputed -- and returned as `figure`,
    which is what the console offers as `<image - run-...-sensitivity_stability>`
    (BUG, 2026-11-02: the answer showed no figure, or not this one).
    """
    if use_module7 and weights is None and scores is None:
        kept = _ranked_choices()
        weights, scores = kept.get("weights"), kept.get("scores")
    payload = _sweep(cloud_pct=cloud_pct, wind_ms=wind_ms, veg_pct=veg_pct,
                     steep_pct=steep_pct, urban_pct=urban_pct, mode=mode,
                     base_weights=base_weights, parameters=parameters,
                     grid_step=grid_step, report_step=report_step, tolerance=tolerance,
                     weights=weights, scores=scores, weighting=weighting)
    if write_figure:
        from pathlib import Path

        from .. import charts

        png = Path(output_dir) / (event_id or "sensitivity") / "sensitivity_stability.png"
        try:
            charts.stability_bands(payload, png, event=event_id)
            payload["figure"] = {
                "name": "sensitivity_stability", "path": str(png),
                "caption": "Weight-stability bands: for each criterion, the weights over "
                           "which the top-ranked product keeps its place (light) and the "
                           "whole order holds (dark); the marker is this event's weight."}
        except Exception as exc:                               # noqa: BLE001
            payload["figure_problem"] = f"{type(exc).__name__}: {exc}"
    return payload
