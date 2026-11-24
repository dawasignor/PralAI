"""Rendering the MCDM payloads into thesis outputs 3, 4 and 5.

**Both documents are deterministic renders, not agent writing.** Output 2 is a
brief because characterising an AOI is judgement; a decision matrix is not. The
numbers here are arithmetic with a specification behind them, and the reasoning
attached to each score is a fixed evidence register with sources. Asking a
model to restate either would put a paraphrase where a citation belongs, and
nothing downstream could tell the difference.

The split between the two documents is deliberate and is the reason output 3
exists at all:

* **Output 3 is the matrix BEFORE the ranking** -- criterion values, the
  evidence behind each one, and the weights. Written first so that the scores
  cannot be read back from the answer they produced.
* **Output 4 is the ranking** -- normalised matrix, ideal and anti-ideal
  points, distances, and the order they imply.

* **Output 5 is the weight sensitivity** -- how far one criterion weight can
  move before that order stops holding. It renders a different payload, from
  `sensitivity_analysis`, and it is here rather than in its own module because
  the three documents share the conventions that make them comparable: the
  same number formatting, the same H1 rule, the same caveat blockquotes.

A reader who disagrees with the outcome has to argue with output 3, where the
evidence is, rather than with the arithmetic in output 4.
"""

from __future__ import annotations

from typing import Any


def _f(value: Any, places: int = 4) -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def _lines() -> tuple[list[str], Any]:
    out: list[str] = []

    def add(text: str = "") -> None:
        out.append(text)

    return out, add


# ---------------------------------------------------------------------------
# Output 3 -- the decision matrix and its evidence
# ---------------------------------------------------------------------------
def decision_matrix_markdown(payload: dict[str, Any]) -> str:
    """The matrix, the evidence behind every score, and the weights."""
    out, add = _lines()
    method = payload["method"]
    gate = payload["gate"]
    matrix = payload["decision_matrix"]
    criteria = payload["criteria"]
    alternatives = payload["alternatives"]
    crit_ids = [c["id"] for c in criteria]

    # No H1 here: `Experiment.write` emits "# 3. <slot title>" above this
    # body, and a second one would leave the document with two titles.
    add("## What this is")
    add()
    add("The decision matrix **before** any ranking: what each candidate flood "
        "product scores on each criterion, why it scores that, and how much "
        "each criterion is weighted for this event.")
    add()
    add("It is written before output 4 on purpose. A matrix published "
        "alongside the ranking it produced invites the scores to be read "
        "backwards from the answer; this one has to stand on its own "
        "evidence.")
    add()
    add(f"Method: **{method['name']}**, mode `{method['mode']}`. "
        f"{method['mode_note']}")
    add()
    if not method.get("wsm_implemented", False):
        add(f"> **{method['wsm_note']}**")
        add()

    # --- the event ---------------------------------------------------------
    add("## The event, as five indices")
    add()
    add("These are the only things about this flood that enter the decision. "
        "Everything else in the matrix is a property of the product.")
    add()
    add("| Index | Value | Read from | Reduced by | A share of |")
    add("|---|---|---|---|---|")
    for entry in payload.get("index_provenance", []):
        add(f"| `{entry['index']}` | {_f(entry['value'])} | "
            f"`{entry['from']}` | {entry['reduction']} | {entry['of']} |")
    add()
    for entry in payload.get("index_provenance", []):
        if entry.get("note"):
            add(f"- **{entry['index']}** -- {entry['note']}")
    add()

    caveats = payload.get("index_caveats") or []
    if caveats:
        add("### What the indices do not say")
        add()
        for text in caveats:
            add(f"- {text}")
        add()

    # --- the gate ----------------------------------------------------------
    add("## Feasibility gate")
    add()
    add(f"Cloud gate: **{gate['cloud_gate_pct']:g}%** AOI cloud cover.")
    add()
    if gate.get("can_fire", True):
        add("Above it, optical products are removed from the candidate set "
            "rather than scored badly -- a sensor that cannot see the ground "
            "is not a worse option, it is not an option.")
    else:
        add("**The gate cannot fire at this setting.** Cloud cover cannot "
            "exceed 100%, so no alternative can ever be excluded by it: every "
            "product is carried into the ranking and cloud acts purely as a "
            "penalty on the weight of `W1`.")
        add()
        add("**Eligibility therefore carries no information in this run.** A "
            "highly-ranked optical product has not been shown to be usable "
            "over this event -- check its `W1` score and the cloud index "
            "before reading a rank as a recommendation.")
    add()
    if gate.get("change_note"):
        # The note is multi-paragraph; a blockquote needs "> " on every
        # line, including the blank ones between paragraphs.
        for para in gate["change_note"].split("\n\n"):
            add("> " + para.replace("\n", "\n> "))
            add(">")
        add()
    if gate["excluded"]:
        add(f"**{len(gate['excluded'])} of {len(alternatives)} alternatives "
            f"were excluded.**")
        add()
        add("| Alternative | Sensor | Reason |")
        add("|---|---|---|")
        for item in gate["excluded"]:
            add(f"| {item['alternative']} | {item['sensor']} | "
                f"{item['reason']} |")
        add()
        add("**The ideal and anti-ideal points in output 4 are built from the "
            "survivors only.** Removing an alternative moves them, so a "
            f"closeness computed among {len(gate['eligible'])} candidates is "
            "not comparable with one computed among "
            f"{len(alternatives)}.")
    else:
        add("No alternative was excluded: all "
            f"{len(alternatives)} were carried into the ranking.")
    add()

    # --- criteria ----------------------------------------------------------
    add("## Criteria")
    add()
    add("| ID | Criterion | Group | Direction | Scale | Scoring rule |")
    add("|---|---|---|---|---|---|")
    for c in criteria:
        direction = "benefit" if c["direction"] == 1 else "cost"
        add(f"| `{c['id']}` | {c['name']} | {c['group']} | {direction} | "
            f"{c['scale']} | {c['rule']} |")
    add()

    # --- the matrix --------------------------------------------------------
    add("## The decision matrix")
    add()
    add("Criteria as rows, because twelve columns do not print. Every weather "
        "and land-characteristic criterion is a cost, and so are spatial resolution and revisit -- "
        "for those, lower is better.")
    add()
    header = "| Criterion | Dir | " + " | ".join(
        a["name"] for a in alternatives) + " |"
    add(header)
    add("|---" * (len(alternatives) + 2) + "|")
    values = matrix["values"]
    for c in criteria:
        row = [f"`{c['id']}` {c['name']}",
               "benefit" if c["direction"] == 1 else "cost"]
        for a in alternatives:
            cell = values[a["id"]][c["id"]]
            row.append(f"{cell:g}" if isinstance(cell, (int, float)) else str(cell))
        add("| " + " | ".join(row) + " |")
    add()
    add("| Alternative | Sensor | Eligible | Product-criteria sources |")
    add("|---|---|---|---|")
    for a in alternatives:
        add(f"| {a['name']} | {a['sensor']} | "
            f"{'yes' if a['eligible'] else 'NO'} | {a['product_sources']} |")
    add()
    add(f"> **P2 is unresolved.** {matrix['p2_pending']}")
    add()
    add("Documented revisit ranges, before the worst case was taken:")
    add()
    add("| Alternative | Revisit range (days) | Value used |")
    add("|---|---|---|")
    for a in alternatives:
        low, high = a["p2_range_days"]
        add(f"| {a['name']} | {low:g}-{high:g} | {high:g} |")
    add()

    # --- evidence ----------------------------------------------------------
    add("## Evidence behind every susceptibility score")
    add()
    add("The product criteria P1-P6 are read from product specifications, "
        "cited per alternative above. The six susceptibility criteria are "
        "coded cell by cell, and each cell carries its basis:")
    add()
    for key, meaning in matrix["basis_meaning"].items():
        add(f"- **{key}** -- {meaning}")
    add()
    add("A cell marked contested is one a second coder could reasonably score "
        "differently. They are listed rather than smoothed over, because a "
        "sensitivity analysis that does not know which cells are soft is "
        "testing the wrong thing.")
    add()
    add("| Criterion | Alternative | Score | Basis | Contested | Rationale | Sources |")
    add("|---|---|---|---|---|---|---|")
    for crit in crit_ids:
        for a in alternatives:
            cell = matrix["evidence"].get(f"{crit}|{a['id']}")
            if cell is None:
                continue
            sources = ", ".join(cell["sources"]) if cell["sources"] else "--"
            add(f"| `{crit}` | {a['name']} | {cell['score']:g} | "
                f"{cell['basis']} | {'**yes**' if cell['contested'] else 'no'} "
                f"| {cell['rationale']} | {sources} |")
    add()

    contested = matrix.get("contested_cells") or []
    if contested:
        add(f"### The {len(contested)} contested cells that carry weight")
        add()
        add("Restricted to alternatives that survived the gate, with the "
            "weight their criterion actually carries in this event.")
        add()
        add("| Criterion | Alternative | Score | Weight | Why it is contested |")
        add("|---|---|---|---|---|")
        for item in contested:
            add(f"| `{item['criterion']}` | {item['alternative']} | "
                f"{item['score']:g} | {_f(item['weight'], 4)} | "
                f"{item['rationale']} |")
        add()

    # --- weights -----------------------------------------------------------
    add("## Weights")
    add()
    event_first = method.get("weighting", "event_first") == "event_first"
    if event_first:
        add("The event does not change what a product scores; it changes how much "
            "each criterion matters. " + method.get("weighting_rule", ""))
        add()
        add("| ID | Criterion | Base | Raised by | Raise | Weight |")
        add("|---|---|---|---|---|---|")
        for c in criteria:
            by = c.get("modulated_by")
            if c.get("increase") is None:
                label, raise_ = "-- product criterion", "share of what is left"
            elif by is None:
                label, raise_ = "-- not raised", "+0%"
            else:
                label, raise_ = f"`{by}` index", f"+{float(c['increase']) * 100:.1f}%"
            add(f"| `{c['id']}` | {c['name']} | {_f(c['base_weight'], 4)} | "
                f"{label} | {raise_} | **{_f(c['weight'], 4)}** |")
        add()
    else:
        add("Specification workbook rule: the effective weight is the base weight "
            "times the modulator (the event index), renormalised to sum to one.")
        add()
        add("| ID | Criterion | Base | Modulated by | Modulator | Effective | Normalised |")
        add("|---|---|---|---|---|---|---|")
        for c in criteria:
            by = c.get("modulated_by")
            label = "--  not modulated" if by is None else f"`{by}` index"
            add(f"| `{c['id']}` | {c['name']} | {_f(c['base_weight'], 4)} | "
                f"{label} | {_f(c['modulator'], 4)} | "
                f"{_f(c['effective_weight'], 6)} | **{_f(c['weight'], 4)}** |")
        add()
    # A modulator of 1.0 is ambiguous on its own, and over a very cloudy event
    # the ambiguity matters: it can mean the criterion is never modulated, or
    # that its index is pinned at maximum.
    maxed = [c for c in criteria
             if c.get("modulated_by") and c["modulator"] >= 1.0]
    zeroed = [c for c in criteria
              if c.get("modulated_by") and c["modulator"] <= 0.0]
    if maxed:
        add("**At maximum** (the index is pinned, not absent): "
            + ", ".join(f"`{c['id']}` via `{c['modulated_by']}`" for c in maxed)
            + ".")
        add()
    if zeroed:
        add("**Zeroed out** -- these criteria carry no weight in this event "
            "and cannot affect the ranking: "
            + ", ".join(f"`{c['id']}` ({c['name']}, `{c['modulated_by']}` "
                        f"index at 0)" for c in zeroed) + ".")
        add()
    add(f"Base weights are **equal ({_f(criteria[0]['base_weight'], 6)} each)**. "
        "That is a placeholder, not a finding: AHP and entropy weighting are "
        "not decided, and an equal-weight ranking says what the criteria imply "
        "when nobody has said which matters more.")
    add()

    # --- parameters --------------------------------------------------------
    add("## Parameters -- every one an author rule")
    add()
    add("None of these is a measurement. A reader who takes the 80% gate as "
        "empirical will over-trust the exclusions it produces.")
    add()
    add("| Parameter | Value | Meaning |")
    add("|---|---|---|")
    for key, value in payload["parameters"].items():
        note = payload["parameter_notes"].get(key, "")
        add(f"| `{key}` | {value:g} | {note} |")
    add()

    # --- sources -----------------------------------------------------------
    add("## Sources")
    add()
    add("| Key | Reference | URL |")
    add("|---|---|---|")
    for key in sorted(payload["sources"], key=lambda k: int(k[1:])):
        entry = payload["sources"][key]
        add(f"| {key} | {entry['ref']} | {entry['url']} |")
    add()
    spec = payload["spec"]
    add(f"Specification: `{spec['workbook']}` ({spec['built']}). "
        f"{spec['note']}")
    add()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Output 4 -- the ranking
# ---------------------------------------------------------------------------
def ranking_markdown(payload: dict[str, Any]) -> str:
    """The normalised matrix, the ideal points, the distances and the order."""
    out, add = _lines()
    method = payload["method"]
    topsis = payload["topsis"]
    gate = payload["gate"]
    criteria = payload["criteria"]
    crit_ids = topsis["criterion_order"]
    rows = topsis["row_order"]

    # See the note in decision_matrix_markdown: the H1 comes from `write`.
    add("## What this is")
    add()
    add("The ranking, and every intermediate table it came from. The scores "
        "and the evidence behind them are in output 3; nothing here revisits "
        "them.")
    add()
    add(f"Method: **{method['name']}**, mode `{method['mode']}`, "
        f"{method['normalisation']} normalisation, {method['rank_rule']}.")
    add()
    if not method.get("wsm_implemented", False):
        add(f"> **{method['wsm_note']}**")
        add()

    # --- the answer --------------------------------------------------------
    changed = payload.get("weights_changed") or []
    if changed:
        add("## The weights you set")
        add()
        add("This ranking uses weights the user stated after Module 6; the others "
            "were rescaled so all twelve still sum to one. Module 6 shows the "
            "computed weights it started from.")
        add()
        add("| ID | Module 6 (computed) | Used here | Stated |")
        add("|---|---|---|---|")
        for row in changed:
            stated = "--" if row.get("stated") is None else _f(row["stated"], 4)
            add(f"| `{row['id']}` | {_f(row['computed'], 4)} | **{_f(row['used'], 4)}** | {stated} |")
        add()
    add("## Ranking")
    add()
    add("| Rank | Alternative | Sensor | Closeness Ci | D+ (to ideal) | D- (to anti-ideal) |")
    add("|---|---|---|---|---|---|")
    for item in payload["ranking"]:
        add(f"| **{item['rank']}** | {item['alternative']} | {item['sensor']} "
            f"| **{_f(item['closeness'], 6)}** | {_f(item['d_plus'], 6)} | "
            f"{_f(item['d_minus'], 6)} |")
    add()
    add("`Ci = D- / (D+ + D-)`: the share of the total distance that lies "
        "between this alternative and the worst one. 1 is the ideal, 0 the "
        "anti-ideal.")
    add()

    if gate["excluded"]:
        add(f"**{len(gate['excluded'])} alternative(s) were never ranked.**")
        add()
        add("| Alternative | Sensor | Reason |")
        add("|---|---|---|")
        for item in gate["excluded"]:
            add(f"| {item['alternative']} | {item['sensor']} | "
                f"{item['reason']} |")
        add()
        add("**This changes what the closeness values mean.** The ideal and "
            "anti-ideal points below are built column-wise from the "
            f"{len(rows)} survivors, so they are the best and worst this "
            "reduced set can offer -- not absolutes. With only two candidates "
            "left, one is at Ci near 1 and the other near 0 by construction, "
            "and the gap between them measures separation within the pair "
            "rather than quality.")
        add()

    # --- normalised --------------------------------------------------------
    add("## Normalised decision matrix")
    add()
    add("Vector normalisation: each column divided by its Euclidean norm over "
        "the eligible alternatives. A column whose norm is zero -- every "
        "candidate identical -- normalises to zero and contributes nothing to "
        "any distance, which is correct: a criterion that does not "
        "discriminate should not move the ranking.")
    add()
    add("| Criterion | " + " | ".join(rows) + " |")
    add("|---" * (len(rows) + 1) + "|")
    for j, crit in enumerate(crit_ids):
        cells = [_f(topsis["normalised"][i][j], 4) for i in range(len(rows))]
        add(f"| `{crit}` | " + " | ".join(cells) + " |")
    add()

    # --- weighted ----------------------------------------------------------
    add("## Weighted normalised matrix")
    add()
    add("Each normalised column multiplied by its criterion weight from "
        "output 3. This is the matrix the distances are measured in.")
    add()
    add("| Criterion | Weight | " + " | ".join(rows) + " |")
    add("|---" * (len(rows) + 2) + "|")
    weights = {c["id"]: c["weight"] for c in criteria}
    for j, crit in enumerate(crit_ids):
        cells = [_f(topsis["weighted"][i][j], 6) for i in range(len(rows))]
        add(f"| `{crit}` | {_f(weights[crit], 4)} | " + " | ".join(cells) + " |")
    add()

    # --- ideal points ------------------------------------------------------
    add("## Ideal and anti-ideal points")
    add()
    add("Built column by column from the weighted matrix above: for a benefit "
        "criterion the ideal is the column maximum and the anti-ideal its "
        "minimum; for a cost criterion they swap. Neither point is an "
        "existing alternative -- both are synthetic.")
    add()
    add("| Criterion | Direction | Ideal best (A+) | Ideal worst (A-) |")
    add("|---|---|---|---|")
    for c in criteria:
        if c["id"] not in crit_ids:
            continue
        add(f"| `{c['id']}` {c['name']} | "
            f"{'benefit' if c['direction'] == 1 else 'cost'} | "
            f"{_f(topsis['ideal_best'][c['id']], 6)} | "
            f"{_f(topsis['ideal_worst'][c['id']], 6)} |")
    add()

    # --- distances ---------------------------------------------------------
    add("## Separation measures")
    add()
    add("Euclidean distance from each alternative to each point, across all "
        "twelve weighted criteria.")
    add()
    add("| Alternative | D+ (to A+) | D- (to A-) | D+ + D- | Ci | Rank |")
    add("|---|---|---|---|---|---|")
    for item in payload["ranking"]:
        total = item["d_plus"] + item["d_minus"]
        add(f"| {item['alternative']} | {_f(item['d_plus'], 6)} | "
            f"{_f(item['d_minus'], 6)} | {_f(total, 6)} | "
            f"{_f(item['closeness'], 6)} | {item['rank']} |")
    add()

    # --- what it rests on --------------------------------------------------
    add("## What this ranking rests on")
    add()
    add("- **Equal base weights.** AHP and entropy weighting are not decided, "
        "so this is what the criteria imply when nobody has said which "
        "matters more.")
    add("- **One method.** Only TOPSIS is implemented. The specification's "
        "weighted-sum robustness check and the Spearman comparison between "
        "the two are not, so the stability of this order under a different "
        "aggregation is untested.")
    contested = payload["decision_matrix"].get("contested_cells") or []
    if contested:
        add(f"- **{len(contested)} contested evidence cells** carry weight in "
            "this ranking. They are listed in output 3 with their weights; a "
            "second coder could score them differently.")
    add(f"- **P2 is unresolved.** {payload['decision_matrix']['p2_pending']}")
    if gate["excluded"]:
        add(f"- **{len(gate['excluded'])} alternatives were removed before "
            "ranking**, which moved the ideal points. The order below is an "
            "order among survivors.")
    add()
    spec = payload["spec"]
    add(f"Specification: `{spec['workbook']}` ({spec['built']}). "
        f"{spec['note']}")
    add()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Output 5 -- the weight sensitivity of the ranking
# ---------------------------------------------------------------------------
def sensitivity_markdown(payload: dict[str, Any]) -> str:
    """The thresholds, the leader changes, the agreement and the curves.

    Same rule as outputs 3 and 4: a deterministic render, not agent writing.
    A threshold is a number produced by a bisection against a stated
    predicate, and a paraphrase of it is not the same claim.
    """
    out, add = _lines()
    method = payload["method"]
    grid = payload["grid"]
    baseline = payload["baseline"]
    criteria = payload["criteria"]
    names = baseline["row_order"]
    # Tags for the order column. Taken from the alternative ids rather than
    # built from the names: "OPERA DSWx-HLS" and "OPERA DSWx-S1" share their
    # first word, and an order column that calls two products the same thing
    # is worse than no order column.
    tag = {r["alternative"]: r["id"].upper() for r in baseline["ranking"]}

    # See the note in decision_matrix_markdown: the H1 comes from `write`.
    add("## What this is")
    add()
    add("How far one criterion weight can move before the ranking in output 4 "
        "stops holding. Each criterion is swept across the whole 0-1 range "
        "with the other eleven held in proportion, and the smallest change "
        "that alters the order is found by bisection.")
    add()
    add(f"Method: **{method['name']}**, mode `{method['mode']}`, "
        f"{method['normalisation']} normalisation, {method['rank_rule']}.")
    add()
    add(f"Construction: {method['construction']}")
    add()
    # Module 8 starts from the weights Module 7 ranked with (user, 2026-11-22).
    add(f"Starting weights: **{payload.get('weights_source', 'computed')}**"
        + (" -- the event-first weights" if method.get("weighting") == "event_first" else "")
        + f"; scores: **{payload.get('scores_source', 'evidence register')}**.")
    for c in payload.get("weights_changed") or []:
        if c.get("stated") is not None:
            add(f"- weight `{c['id']}` set to {c['stated']:g} (used {c['used']:.4f} after "
                f"rescaling; computed {c['computed']:.4f})")
    for c in payload.get("scores_changed") or []:
        add(f"- score `{c['criterion']}` for {c['alternative']}: {c['was']:g} -> {c['now']:g}")
    add()
    add(f"Search: {method['search']}")
    add()
    add("> **This perturbs criterion weights and nothing else.** The product "
        "scores, the contested evidence cells, the unresolved P2 revisit "
        "range, the author parameters and the five event indices are all held "
        "at the values output 3 records. A ranking that survives every weight "
        "in this document has not been shown to survive a different score.")
    add()
    if not method.get("wsm_implemented", False):
        add(f"> **{method['wsm_note']}**")
        add()

    # --- the baseline being measured from ---------------------------------
    add("## The ranking being tested")
    add()
    add("| Rank | Alternative | Closeness Ci | Gap to the rank below |")
    add("|---|---|---|---|")
    ordered = sorted(baseline["ranking"], key=lambda r: r["rank"])
    for position, item in enumerate(ordered):
        below = ordered[position + 1] if position + 1 < len(ordered) else None
        gap = ("--" if below is None
               else _f(item["closeness"] - below["closeness"], 6))
        add(f"| **{item['rank']}** | {item['alternative']} | "
            f"{_f(item['closeness'], 6)} | {gap} |")
    add()
    pair = baseline["closest_pair"]
    add(f"The closest pair is **{pair['upper']}** (rank {pair['ranks'][0]}) and "
        f"**{pair['lower']}** (rank {pair['ranks'][1]}), separated by "
        f"**{_f(pair['gap'], 6)}** in closeness. That gap is what most of the "
        "thresholds below are measuring against.")
    add()

    # --- the headline ------------------------------------------------------
    add("## The smallest weight change that changes the order")
    add()
    add("For each criterion: the nearest weight, in either direction, at which "
        "the rank vector stops being the one above. `Delta` is the move from "
        "the weight the event gave it; `relative` expresses that as a share of "
        "that weight, which is the fairer comparison when the weights are not "
        "equal.")
    add()
    add("| Criterion | Name | Weight | Changes at | Delta | Relative | First swap | Weight to state |")
    add("|---|---|---|---|---|---|---|---|")
    for c in criteria:
        near = c["nearest_change"]
        if near is None:
            add(f"| `{c['criterion']}` | {c['name']} | {_f(c['weight'], 4)} | "
                f"**no change in [0, 1]** | -- | -- | -- | -- |")
            continue
        swap = " / ".join(f"{m['alternative']} {m['from']}->{m['to']}"
                          for m in near["moved"])
        rel = ("--" if near["relative_delta"] is None
               else f"{near['relative_delta'] * 100:+.1f}%")
        stated = near["equivalent_stated_weight"]
        add(f"| `{c['criterion']}` | {c['name']} | {_f(c['weight'], 4)} | "
            f"{_f(near['weight'], 4)} | {near['delta']:+.4f} | {rel} | {swap} | "
            f"{'--' if stated is None else _f(stated, 4)} |")
    add()
    add("**Weight to state** is the number to give the ranking for that "
        "criterion -- \"set L1 to ...\" -- with the other eleven kept as Module 7 "
        "used them; the ranking renormalises, which puts the criterion exactly at "
        "the threshold. A value above 1 is meaningful rather than out of range: "
        "it means that criterion must outweigh all the others together.")
    add()

    critical = payload.get("most_critical")
    reachable_critical = payload.get("most_critical_reachable")
    if critical:
        swap = " and ".join(f"{m['alternative']} ({m['from']} -> {m['to']})"
                            for m in critical["moved"])
        rel = ("" if critical["relative_delta"] is None
               else f", {abs(critical['relative_delta']) * 100:.1f}% of its own weight")
        add(f"**The most critical criterion is `{critical['criterion']}` "
            f"({critical['name']}).** Its weight has to move "
            f"{abs(critical['delta']):.4f}{rel} -- from "
            f"{_f(critical['weight'], 4)} to "
            f"{_f(critical['threshold_weight'], 4)} -- before the order "
            f"changes, and what changes is {swap}.")
        add()
        if (reachable_critical
                and reachable_critical["criterion"] != critical["criterion"]):
            add(f"The most critical **reachable** criterion is "
                f"`{reachable_critical['criterion']}` "
                f"({reachable_critical['name']}), at "
                f"{abs(reachable_critical['delta']):.4f}.")
            add()

    stable = payload.get("stable_criteria") or []
    if stable:
        add("**No weight anywhere in [0, 1] changes the order for "
            + ", ".join(f"`{s}`" for s in stable)
            + ".** That is the strongest stability result available here: "
            "those criteria cannot reorder this ranking however they are "
            "weighted.")
    else:
        add("**Every criterion can change the order somewhere in [0, 1].** "
            "No criterion is inert; the table above is about how far each has "
            "to move, not whether it can.")
    add()

    # --- the leader --------------------------------------------------------
    add("## When the top-ranked product changes")
    add()
    add(f"The order changing and the *recommendation* changing are different "
        f"events. This table asks only the second: at what weight does "
        f"**{baseline['leader']}** stop being first.")
    add()
    add("| Criterion | Weight | Leader changes at | Delta | New leader | Weight to state |")
    add("|---|---|---|---|---|---|")
    for c in criteria:
        near = c["nearest_leader_change"]
        if near is None:
            add(f"| `{c['criterion']}` | {_f(c['weight'], 4)} | "
                f"**never in [0, 1]** | -- | -- | -- |")
            continue
        stated = near["equivalent_stated_weight"]
        add(f"| `{c['criterion']}` | {_f(c['weight'], 4)} | "
            f"{_f(near['weight'], 4)} | {near['delta']:+.4f} | "
            f"{near['leader']} | "
            f"{'--' if stated is None else _f(stated, 4)} |")
    add()
    movers = payload.get("leader_changeable_by") or []
    if movers:
        add(f"**{len(movers)} of {len(criteria)} criteria can displace "
            f"{baseline['leader']}**: "
            + ", ".join(f"`{m}`" for m in movers)
            + ". The rest cannot, at any weight.")
    else:
        add(f"**No single criterion can displace {baseline['leader']}** at any "
            "weight in [0, 1]. The top of this ranking does not rest on one "
            "criterion.")
    add()

    # --- agreement ---------------------------------------------------------
    add("## Rank agreement across each sweep")
    add()
    add(f"Each sweep is walked on the search grid "
        f"({grid['grid_step']} in weight units, "
        f"{criteria[0]['agreement']['grid_points']} points from 0 to 1) and "
        "every perturbed ranking is compared with the baseline. Spearman's "
        "rho and Kendall's tau-b are computed on the closeness values with "
        "ties averaged. **The fractions are shares of the weight range, not "
        "probabilities** -- nothing here says a weight is likely, only that it "
        "is possible.")
    add()
    add("| Criterion | Weight | Worst rho | Worst tau-b | Mean rho | Order unchanged | Leader unchanged |")
    add("|---|---|---|---|---|---|---|")
    for c in criteria:
        a = c["agreement"]
        add(f"| `{c['criterion']}` | {_f(c['weight'], 4)} | "
            f"{a['min_spearman']:+.3f} | {a['min_kendall_tau_b']:+.3f} | "
            f"{a['mean_spearman']:+.3f} | "
            f"{a['order_unchanged_fraction'] * 100:.1f}% | "
            f"{a['leader_unchanged_fraction'] * 100:.1f}% |")
    add()
    add("A negative worst-rho means some weight in the range inverts the "
        "ranking rather than merely disturbing it. A low *order unchanged* "
        "against a high *leader unchanged* means the criterion reshuffles the "
        "middle of the table while the recommendation stands.")
    add()

    # --- the curves --------------------------------------------------------
    add("## Closeness response curves")
    add()
    add(f"Closeness at every {grid['report_step']} of weight, per criterion. "
        "The baseline weight lies between two of these columns; the printed "
        "grid is for reading the shape, and no threshold above was computed "
        "from it. The ends are degenerate on purpose: at 0 the criterion is "
        "deleted, at 1 it is the only criterion left.")
    add()
    for c in criteria:
        add(f"### `{c['criterion']}` {c['name']} "
            f"-- weight {_f(c['weight'], 4)}"
            + ("" if c["reachable"] else ", unreachable"))
        add()
        if c["reachability_note"]:
            add(f"> {c['reachability_note']}")
            add()
        add("| Weight | " + " | ".join(names) + " | Order |")
        add("|---" * (len(names) + 2) + "|")
        for point in c["curve"]:
            order = " > ".join(tag[n] for n in point["order"])
            add(f"| {point['weight']:.2f} | "
                + " | ".join(_f(point["closeness"][n], 4) for n in names)
                + f" | {order} |")
        add()
    add("Tags in the order column: "
        + ", ".join(f"`{tag[n]}` = {n}" for n in names) + ".")
    add()

    # --- what it does not say ---------------------------------------------
    add("## What this does and does not test")
    add()
    add("- **Weights only.** " + "; ".join(method["does_not_perturb"]) +
        " are all held fixed. A ranking stable under every weight here may "
        "still be fragile to one contested score.")
    add("- **One weight at a time.** Two criteria moving together can cross a "
        "threshold that neither crosses alone, and this construction cannot "
        "see it. That is what output 6 (SMAA) is for -- it samples the whole "
        "weight space rather than twelve lines through it.")
    add("- **The whole range is swept, and most of it is not plausible.** A "
        "weight of 0.9 on one criterion is a different decision model, not a "
        "different opinion. Read the thresholds, which are local; the "
        "fractions are context.")
    add("- **A crossing narrower than the search grid is invisible.** The grid "
        f"is {grid['grid_step']} in weight units. An order that changes and "
        "changes back inside one step reads as no change at all.")
    add("- **The baseline was recomputed and checked** against the ranking in "
        "output 4 before any sweep ran; a disagreement above 1e-9 in closeness "
        "raises rather than reporting thresholds measured from the wrong "
        "place.")
    add("- **One method.** Only TOPSIS is implemented, so this says how "
        "stable the TOPSIS order is, not how stable the decision is.")
    add()
    spec = payload["spec"]
    add(f"Specification: `{spec['workbook']}` ({spec['built']}). "
        f"{spec['sensitivity_note']}")
    add()
    return "\n".join(out)
