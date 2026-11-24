"""The weight sensitivity of the product ranking: output 5's arithmetic.

    python tests/test_sensitivity.py

Offline. No model, no network, no cost -- `sensitivity_analysis` fetches
nothing, and neither does the workflow it rests on.

Two kinds of check. The first is against a **closed form**: a two-by-two
decision problem whose crossing can be written down, so the bisected threshold
can be compared with the exact answer rather than with a previous run of the
same code. The second is against the ranking itself -- that the baseline the
sweep measures from is the one `rank_flood_products` produces, because a
threshold measured from the wrong baseline is wrong in a way no reader of the
output could detect.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import pathlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import workflows                                   # noqa: E402
from core.charts import stability_bands, stability_rows      # noqa: E402
from core.mcdm_report import sensitivity_markdown            # noqa: E402
from core.workflows.mcdm import rank_flood_products          # noqa: E402
from core.workflows.sensitivity import (                     # noqa: E402
    _first_change, _state, kendall_tau_b, midranks, perturbed_weights,
    sensitivity_analysis, spearman,
)

FAILURES: list[str] = []

# The Kelani event, from experiments/kelani-2025-west/inputs/mcdm.json. Written
# here rather than read from the cache so this test stays offline and
# independent of whether that experiment directory exists.
KELANI = dict(cloud_pct=91.673698, wind_ms=4.134169, veg_pct=61.26,
              steep_pct=9.85, urban_pct=0.193297)


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  FAIL: {message}")


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def check_registered() -> None:
    """It is callable by name, like every other workflow."""
    names = workflows.names()
    print(f"  registry holds {len(names)} workflows")
    if "sensitivity_analysis" not in names:
        fail("sensitivity_analysis is not registered")
        return
    entry = workflows.get("sensitivity_analysis")
    print(f"  description: {entry.description[:60]}...")
    if "weight" not in entry.description.lower():
        fail("the registered description does not say it perturbs weights; "
             "that description is the only text about this workflow that ever "
             "reaches a prompt")


def check_rank_correlations() -> None:
    """The tie-safe formulas, against values that can be worked out by hand."""
    cases = [
        ("identical", [4.0, 3.0, 2.0, 1.0], [4.0, 3.0, 2.0, 1.0], 1.0, 1.0),
        ("reversed", [4.0, 3.0, 2.0, 1.0], [1.0, 2.0, 3.0, 4.0], -1.0, -1.0),
        ("monotone rescale", [4.0, 3.0, 2.0, 1.0], [40.0, 9.0, 4.0, 0.5],
         1.0, 1.0),
    ]
    for label, a, b, rho, tau in cases:
        got_rho, got_tau = spearman(a, b), kendall_tau_b(a, b)
        print(f"  {label:<18} rho={got_rho:+.4f} tau={got_tau:+.4f}")
        if not close(got_rho, rho, 1e-12):
            fail(f"spearman({label}) = {got_rho}, expected {rho}")
        if not close(got_tau, tau, 1e-12):
            fail(f"kendall({label}) = {got_tau}, expected {tau}")

    # One swapped adjacent pair out of four: sum d^2 = 2, so rho = 1 - 12/60.
    one_swap = spearman([4.0, 3.0, 2.0, 1.0], [4.0, 2.0, 3.0, 1.0])
    print(f"  one adjacent swap  rho={one_swap:+.4f}")
    if not close(one_swap, 0.8, 1e-12):
        fail(f"one adjacent swap gave rho={one_swap}, expected 0.8")

    # Ties must be averaged, not left at the competition rank: two tied values
    # in second place share rank 2.5, and a formula that assumes distinct
    # ranks quietly returns a different number.
    tied = midranks([5.0, 3.0, 3.0, 1.0])
    print(f"  midranks with a tie: {tied}")
    if tied != [1.0, 2.5, 2.5, 4.0]:
        fail(f"midranks did not average the tie: {tied}")
    if not close(kendall_tau_b([5.0, 3.0, 3.0, 1.0], [5.0, 3.0, 3.0, 1.0]),
                 1.0, 1e-12):
        fail("tau-b of a tied vector with itself is not 1")


def check_perturbation_is_proportional() -> None:
    """The other weights keep their ratios and the twelve still sum to 1."""
    weights = [0.5, 0.3, 0.2]
    moved = perturbed_weights(weights, 0, 0.8)
    print(f"  {weights} -> {[round(float(w), 6) for w in moved]}")
    if not close(float(moved[0]), 0.8):
        fail(f"the swept criterion is at {moved[0]}, expected 0.8")
    if not close(float(moved.sum()), 1.0):
        fail(f"weights sum to {moved.sum()}, expected 1")
    before = weights[1] / weights[2]
    after = float(moved[1]) / float(moved[2])
    if not close(before, after):
        fail(f"the untouched criteria changed ratio: {before} -> {after}")

    # Zero and one are both in range: deleting a criterion and giving it the
    # whole decision are the two ends every sweep runs to.
    for target in (0.0, 1.0):
        edge = perturbed_weights(weights, 1, target)
        if not close(float(edge[1]), target) or not close(float(edge.sum()), 1.0):
            fail(f"the sweep does not reach {target}: {edge}")


def check_threshold_against_closed_form() -> None:
    """A crossing that can be written down, found by bisection.

    Two alternatives, two benefit criteria, an identity matrix. Vector
    normalisation leaves it unchanged, so the weighted matrix is
    diag(w1, w2), the ideal is (w1, w2) and the anti-ideal (0, 0). Then

        Ci(A1) = w1 / (w1 + w2)      Ci(A2) = w2 / (w1 + w2)

    and the two are equal exactly at w1 = w2 = 0.5. A grid alone cannot land
    on that; the bisection has to.
    """
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]])
    directions = np.array([1, 1])
    weights = [0.6, 0.4]
    baseline_ranks, baseline_closeness = _state(
        matrix, np.array(weights), directions)
    print(f"  baseline Ci: {[round(c, 6) for c in baseline_closeness]}, "
          f"ranks {baseline_ranks}")
    if baseline_ranks != (1, 2):
        fail(f"the synthetic baseline is not A1 then A2: {baseline_ranks}")

    found = _first_change(
        matrix, directions, weights, 0, start=0.6, stop=0.0, step=0.05,
        tolerance=1e-12, changed=lambda ranks: ranks != baseline_ranks)
    if found is None:
        fail("no crossing found where the closed form says there is one")
        return
    print(f"  crossing at w1 = {found['weight']:.12f}, exact 0.5")
    if not close(found["weight"], 0.5, 1e-9):
        fail(f"bisected crossing {found['weight']}, closed form 0.5")

    # And it is a real crossing, not a numerical flicker: the order holds just
    # inside it and differs just outside.
    inside, _ = _state(matrix, perturbed_weights(weights, 0, 0.5 + 1e-6),
                       directions)
    outside, _ = _state(matrix, perturbed_weights(weights, 0, 0.5 - 1e-6),
                        directions)
    if inside != baseline_ranks:
        fail("the order had already changed before the threshold")
    if outside == baseline_ranks:
        fail("the order had not changed past the threshold")


def check_no_crossing_is_reported_as_none() -> None:
    """A criterion that cannot reorder anything returns None, not a number.

    Both alternatives identical on the swept criterion: its column normalises
    to a constant, contributes the same distance to each, and no weight
    changes the order. `None` is the finding -- a threshold invented here
    would read as fragility that does not exist.
    """
    matrix = np.array([[1.0, 2.0], [1.0, 1.0]])
    directions = np.array([1, 1])
    weights = [0.5, 0.5]
    baseline_ranks, _ = _state(matrix, np.array(weights), directions)
    found = _first_change(
        matrix, directions, weights, 0, start=0.5, stop=1.0, step=0.01,
        tolerance=1e-12, changed=lambda ranks: ranks != baseline_ranks)
    print(f"  identical column, sweep to 1.0: {found}")
    if found is not None:
        fail(f"a non-discriminating criterion reported a threshold: {found}")


def check_baseline_is_the_ranking() -> None:
    """The sweep measures from the ranking output 4 publishes, exactly."""
    ranked = rank_flood_products(**KELANI)
    result = sensitivity_analysis(**KELANI)

    stated = {r["alternative"]: r["closeness"] for r in ranked["ranking"]}
    drift = max(abs(stated[name] - value)
                for name, value in result["baseline"]["closeness"].items())
    print(f"  max closeness drift: {drift:.3e}")
    if drift > 1e-12:
        fail(f"the sweep's baseline differs from the ranking by {drift:.3e}")

    order = [r["alternative"] for r in sorted(ranked["ranking"],
                                              key=lambda r: r["rank"])]
    if result["baseline"]["order"] != order:
        fail(f"baseline order {result['baseline']['order']} != {order}")
    print(f"  order: {' > '.join(order)}")

    weights = {c["id"]: c["weight"] for c in ranked["criteria"]}
    for cid, value in result["baseline"]["weights"].items():
        if not close(weights[cid], value, 1e-12):
            fail(f"weight {cid}: {value} != {weights[cid]}")


def check_thresholds_are_real_crossings() -> None:
    """Every reported threshold holds the order on one side and not the other.

    This is the check that would catch a sweep measuring the right quantity
    from the wrong place: a threshold is only a threshold if the order is
    still the baseline one just short of it.
    """
    result = sensitivity_analysis(**KELANI)
    baseline_ranks = tuple(
        next(r["rank"] for r in result["baseline"]["ranking"]
             if r["alternative"] == name)
        for name in result["baseline"]["row_order"])

    ranked = rank_flood_products(**KELANI)
    criteria = ranked["topsis"]["criterion_order"]
    by_name = {a["name"]: a["id"] for a in ranked["alternatives"]}
    ids = [by_name[n] for n in ranked["topsis"]["row_order"]]
    values = ranked["decision_matrix"]["values"]
    matrix = np.array([[values[a][c] for c in criteria] for a in ids],
                      dtype=float)
    directions = np.array([ranked["decision_matrix"]["directions"][c]
                           for c in criteria], dtype=int)
    weights = [result["baseline"]["weights"][c] for c in criteria]

    checked = 0
    for entry in result["criteria"]:
        near = entry["nearest_change"]
        if near is None:
            continue
        index = criteria.index(entry["criterion"])
        step = math.copysign(1e-6, near["delta"])
        just_short, _ = _state(
            matrix, perturbed_weights(weights, index, near["weight"] - step),
            directions)
        at_or_past, _ = _state(
            matrix, perturbed_weights(weights, index, near["weight"] + step),
            directions)
        if just_short != baseline_ranks:
            fail(f"{entry['criterion']}: the order had already changed "
                 f"before the reported threshold {near['weight']}")
        if at_or_past == baseline_ranks:
            fail(f"{entry['criterion']}: the order had not changed past the "
                 f"reported threshold {near['weight']}")
        if not 0.0 <= near["weight"] <= 1.0:
            fail(f"{entry['criterion']}: threshold {near['weight']} is "
                 "outside [0, 1]")
        checked += 1
    print(f"  {checked} thresholds bracket a real change of order")

    critical = result["most_critical"]
    smallest = min(abs(c["nearest_change"]["delta"])
                   for c in result["criteria"]
                   if c["nearest_change"] is not None)
    print(f"  most critical: {critical['criterion']} at "
          f"{abs(critical['delta']):.6f} (smallest {smallest:.6f})")
    if not close(abs(critical["delta"]), smallest, 1e-12):
        fail("most_critical is not the criterion with the smallest change")


def check_equivalent_stated_weight() -> None:
    """The weight a threshold says to state actually produces it.

    The claim in the output is that the user could type this number ("set L1
    to ..."). So it is checked by typing it: the threshold weight has to come
    back out of `rank_flood_products` when that weight is stated.
    """
    result = sensitivity_analysis(**KELANI)
    tested = 0
    for entry in result["criteria"]:
        near = entry["nearest_change"]
        if near is None or near["equivalent_stated_weight"] is None:
            continue
        again = rank_flood_products(
            **KELANI, weights={entry["criterion"]: near["equivalent_stated_weight"]})
        produced = next(c["weight"] for c in again["criteria"]
                        if c["id"] == entry["criterion"])
        if not close(produced, near["weight"], 1e-9):
            fail(f"{entry['criterion']}: stated weight "
                 f"{near['equivalent_stated_weight']:.6f} produces weight "
                 f"{produced:.9f}, not the threshold {near['weight']:.9f}")
        tested += 1
    print(f"  {tested} equivalent stated weights reproduce their threshold")
    if tested == 0:
        fail("no equivalent stated weight was testable")


def check_every_weight_is_reachable() -> None:
    """Event-first weighting (2026-11-22): no criterion is ever zero, and any
    weight can be stated, so nothing is flagged unreachable."""
    result = sensitivity_analysis(**KELANI)
    for entry in result["criteria"]:
        if not entry["reachable"] or entry["reachability_note"]:
            fail(f"{entry['criterion']} is flagged unreachable")
        if entry["weight"] <= 0.0:
            fail(f"{entry['criterion']} has weight 0 under event-first weighting")
    print("  every criterion reachable, none at weight 0")


def check_module_8_starts_from_module_7() -> None:
    """Module 8 sweeps around the weights and scores Module 7 ranked with,
    including the user's changes (2026-11-22)."""
    from core import experiment

    ranked = rank_flood_products(**KELANI, weights={"L1": 0.2}, scores={"P2": {"gfm": 6}})

    class Store:
        def cached_input(self, name):
            return {"payload": ranked} if name in ("mcdm", "rank_flood_products") else None

    experiment.set_current(Store())
    try:
        result = sensitivity_analysis(**KELANI, write_figure=False)
        free = sensitivity_analysis(**KELANI, write_figure=False, use_module7=False)
    finally:
        experiment.set_current(None)
    used = {c["id"]: c["weight"] for c in ranked["criteria"]}
    swept = result["baseline"]["weights"]
    if any(not close(swept[c], used[c], 1e-12) for c in used):
        fail(f"Module 8 baseline weights {swept} are not Module 7's {used}")
    if result["scores_changed"] != ranked["scores_changed"]:
        fail("Module 8 did not keep Module 7's changed scores")
    if result["weights_source"] != "user-stated at Module 7":
        fail(f"weights_source is {result['weights_source']!r}")
    if free["weights_changed"] or free["scores_changed"]:
        fail("use_module7=False still picked up the stored changes")
    print("  Module 8 sweeps around Module 7's weights and scores")


def check_deterministic_and_serialisable() -> None:
    """Same inputs, same JSON. It is a cached thesis input like any other."""
    first = json.dumps(sensitivity_analysis(**KELANI), sort_keys=True,
                       allow_nan=False)
    second = json.dumps(sensitivity_analysis(**KELANI), sort_keys=True,
                        allow_nan=False)
    print(f"  payload: {len(first):,} bytes of JSON")
    if first != second:
        fail("two runs on the same inputs produced different payloads")


def check_report_renders() -> None:
    """Output 5 renders, and says the things it must not omit."""
    result = sensitivity_analysis(**KELANI)
    text = sensitivity_markdown(result)
    print(f"  rendered {len(text.splitlines())} lines")
    if text.lstrip().startswith("# "):
        fail("the render emits its own H1; Experiment.write supplies it")
    required = [
        "## The smallest weight change that changes the order",
        "## When the top-ranked product changes",
        "## Rank agreement across each sweep",
        "## Closeness response curves",
        "## What this does and does not test",
        "perturbs criterion weights and nothing else",
        result["baseline"]["leader"],
    ]
    for phrase in required:
        if phrase not in text:
            fail(f"the render is missing {phrase!r}")
    if result["most_critical"] and (
            f"`{result['most_critical']['criterion']}`" not in text):
        fail("the most critical criterion is not named in the render")
    # Every alternative needs an unambiguous tag: two products whose names
    # share a first word must not collapse to the same one.
    tags = [r["id"].upper() for r in result["baseline"]["ranking"]]
    if len(set(tags)) != len(tags):
        fail(f"the order column's tags are not unique: {tags}")


def check_grid_is_validated() -> None:
    """A grid coarser than the answer is refused, not quietly accepted."""
    for kwargs in ({"grid_step": 0.0}, {"grid_step": 0.5},
                   {"report_step": 0.0}, {"tolerance": 0.0}):
        try:
            sensitivity_analysis(**KELANI, **kwargs)
        except ValueError as exc:
            print(f"  {kwargs} -> {str(exc)[:54]}...")
        else:
            fail(f"{kwargs} was accepted")


def check_stability_figure() -> None:
    """The figure is a redraw of the payload, and says so by matching it.

    Every band edge is checked back against the thresholds the document was
    rendered from. A figure that quietly recomputed anything could disagree
    with output 5 while looking perfectly reasonable, and nobody compares a
    picture with a table cell by cell.
    """
    result = sensitivity_analysis(**KELANI)
    rows = stability_rows(result)
    by_id = {r["criterion"]: r for r in rows}
    print(f"  {len(rows)} rows, order narrowest first")

    for entry in result["criteria"]:
        row = by_id[entry["criterion"]]
        for edge, key in ((0, "down"), (1, "up")):
            change = entry["first_change"][key]
            drawn, is_open = row["order"][edge], row["order"][edge + 2]
            if change is None:
                if not is_open or drawn != (0.0 if edge == 0 else 1.0):
                    fail(f"{entry['criterion']}: no {key} threshold, but the "
                         f"figure draws a closed edge at {drawn}")
            elif is_open or abs(drawn - change["weight"]) > 1e-12:
                fail(f"{entry['criterion']}: figure edge {drawn} != threshold "
                     f"{change['weight']}")

    # Unreachable criteria go last, whatever their band width -- they are not
    # the weak link, and a reader scans from the top.
    reachable_flags = [r["reachable"] for r in rows]
    if sorted(reachable_flags, reverse=True) != reachable_flags:
        fail(f"unreachable criteria are not last: "
             f"{[(r['criterion'], r['reachable']) for r in rows]}")
    widths = [r["width"] for r in rows if r["reachable"]]
    if widths != sorted(widths):
        fail(f"reachable rows are not narrowest-first: {widths}")

    # The nested drawing is only honest if the inner band is inside the outer
    # one; `stability_rows` refuses otherwise. Check it refuses.
    broken = json.loads(json.dumps(result))
    victim = broken["criteria"][0]
    victim["leader_change"]["up"] = {"weight": victim["weight"]}
    try:
        stability_rows(broken)
    except ValueError as exc:
        print(f"  containment guard fires: {str(exc)[:56]}...")
    else:
        fail("a leader band narrower than the order band was accepted")

    with tempfile.TemporaryDirectory() as folder:
        out = pathlib.Path(folder) / "fig.png"
        row = stability_bands(result, out, event="test")
        size = out.stat().st_size
        print(f"  rendered {size:,} B")
        if size < 20_000:
            fail(f"the rendered figure is only {size} bytes")
        if len(row["rows"]) != len(result["criteria"]):
            fail("the index row does not describe every criterion")
        if "not a plausibility claim" not in row["marks"]:
            fail("the index row does not disclaim the half/double marks")


def main() -> int:
    hr("1. the workflow is registered")
    check_registered()
    hr("2. rank correlations, including ties")
    check_rank_correlations()
    hr("3. the perturbation keeps the other weights in proportion")
    check_perturbation_is_proportional()
    hr("4. a threshold against a closed form")
    check_threshold_against_closed_form()
    hr("5. a criterion that cannot reorder reports no threshold")
    check_no_crossing_is_reported_as_none()
    hr("6. the baseline is the published ranking")
    check_baseline_is_the_ranking()
    hr("7. every threshold brackets a real change of order")
    check_thresholds_are_real_crossings()
    hr("8. the equivalent stated weight reproduces its threshold")
    check_equivalent_stated_weight()
    hr("9. every weight is reachable under event-first weighting")
    check_every_weight_is_reachable()
    hr("9b. Module 8 starts from the weights and scores Module 7 used")
    check_module_8_starts_from_module_7()
    hr("10. deterministic and JSON-serialisable")
    check_deterministic_and_serialisable()
    hr("11. output 5 renders")
    check_report_renders()
    hr("12. the search grid is validated")
    check_grid_is_validated()
    hr("13. the stability figure redraws the payload, nothing more")
    check_stability_figure()

    print()
    if FAILURES:
        print(f"{'=' * 68}\n{len(FAILURES)} CHECK(S) FAILED\n{'=' * 68}")
        for message in FAILURES:
            print(f"  - {message}")
        return 1
    print(f"{'=' * 68}\nALL CHECKS PASSED\n{'=' * 68}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
