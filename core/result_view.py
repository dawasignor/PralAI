"""What Argus is shown of a Monty result -- the readable view, not raw JSON.

Monty's results used to reach Argus as `json.dumps(results)[:4000]`: the raw
payload, cut at a fixed length in the order its keys happen to appear. For
`fetch_weather` that order is meta, grid, then 30+ KB of hourly arrays,
then `event_summary` -- so the cut always fell inside the hourly arrays and the
rainfall, wind and cloud totals never reached the answer. A live console run
said exactly that (2026-10-14).

The thesis outputs never had the problem, because they were never made from a
prefix. Outputs 4 and 5 are `core/mcdm_report`; this module gives Argus **those
same renderers**, so the console answer and the thesis document read the
numbers from one place and cannot disagree.

Weather gets the **summarizer** instead of the full document (since
2026-10-14): the few values output 1 is written from, read from the same
payload, with the hyetograph shown in place of the rainfall table. The
document's sampling-grid table, daily rollup and standing caveats are for a
reader of `01_weather_data.md`, not for writing an answer.

    fetch_weather         weather_summary.markdown(summarize(...))
    rank_flood_products   mcdm_report.ranking_markdown   (output 4; the 20 KB
                          evidence register of output 3 stays in the payload)
    sensitivity_analysis  mcdm_report.sensitivity_markdown

Any other workflow is **condensed**: every scalar and small block kept, every
long list replaced by one line saying how long it was and, for numbers, its
min / max / mean. Nothing is recomputed beyond that summary line.

**A cut is never silent.** A report longer than the budget keeps whole sections
in order and names the ones left out; a condensed view still over budget ends
with a `[TRUNCATED ...]` marker. The full payload stays in the run state
(`monty_result`) and, for an experiment run, in its cache.
"""

from __future__ import annotations

import json
from typing import Any, Callable

# Characters per result, about 3,000 tokens. A rendered report is sized in
# whole sections up to this: the weather and ranking reports fit whole
# (6-7 KB), the sensitivity report (about 25 KB) is cut at a section boundary.
REPORT_BUDGET = 12_000
# The condensed-JSON fallback gets the same budget. At 6,000 the condensed
# land-cover payload (10.4 KB) still lost its later indices, its SCL counts
# and its scene-selection record; at 12,000 land cover, terrain and permanent
# water all arrive whole (measured on kelani-2025-west, 2026-10-14).
JSON_BUDGET = 12_000
LIST_KEEP = 12


def _weather(payload: dict[str, Any]) -> str:
    from .weather_summary import markdown, summarize
    return markdown(summarize(payload))


def _ranking(payload: dict[str, Any]) -> str:
    from .mcdm_report import ranking_markdown
    return ranking_markdown(payload)


def _sensitivity(payload: dict[str, Any]) -> str:
    from .mcdm_report import sensitivity_markdown
    return sensitivity_markdown(payload)


def _matrix(payload: dict[str, Any]) -> str:
    """Module 6 (user, 2026-11-22): the answer read like the old white table --
    numbers with no sign of which rows are costs, and a weight story the
    agent worked out (wrongly: "vegetation RAISES L1"). Argus gets the
    reading lines written from the payload, the changes the user made, and
    the shape of a short answer; the matrix itself is the figure."""
    out = ["## Module 6 at a glance",
           "The decision matrix is drawn as one figure (`mcdm_decision_matrix` in the "
           "figure list): show it; never retype the matrix as a table.", "",
           "### How to read it (use these lines as written; every number is the tool's)"]
    out += [f"- {line}" for line in payload.get("reading_guide") or []]
    weights = {c["id"]: c for c in payload.get("criteria") or []}
    top = sorted(weights.values(), key=lambda c: -float(c.get("weight") or 0))
    out += ["", "### Weights for this event (after rescaling)",
            "- " + ", ".join(f"{c['id']} {float(c.get('weight') or 0):.3f}" for c in top)]
    changed_w = [c for c in payload.get("weights_changed") or [] if c.get("stated") is not None]
    changed_s = payload.get("scores_changed") or []
    out += ["", "### Changed by the user"]
    if not (changed_w or changed_s):
        out.append("- nothing: computed weights, documented scores")
    out += [f"- weight {c['id']}: set to {c['stated']:g}, used {c['used']:.3f} after rescaling "
            f"(computed was {c['computed']:.3f})" for c in changed_w]
    out += [f"- score {c['criterion']} for {c['alternative']}: {c['was']:g} -> {c['now']:g}"
            for c in changed_s]
    contested = (payload.get("decision_matrix") or {}).get("contested_cells") or []
    if contested:
        out += ["", "### Contested scores (a second scorer could score these differently)"]
        out += [f"- {c['criterion']} {c['alternative']}: {c['score']:g}" for c in contested]
    out += ["", "### Shape of the answer",
            "One sentence; the figure; the reading lines as short bullets; one line on "
            "the next step: Module 7 ranks with these weights, and any weight or score can "
            "be changed first (for example \"set L1 to 0.2\" or \"give GFM a revisit of 6 "
            "days\"). No other tables."]
    return "\n".join(out)


RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "mcdm_decision_matrix": _matrix,
    "fetch_weather": _weather,
    "rank_flood_products": _ranking,
    "sensitivity_analysis": _sensitivity,
}


def fit_sections(markdown: str, budget: int = REPORT_BUDGET) -> str:
    """Whole `## ` sections in order until the budget, then name what was left out."""
    if len(markdown) <= budget:
        return markdown
    head, *rest = markdown.split("\n## ")
    sections = [head] + ["## " + s for s in rest]
    kept: list[str] = []
    used = 0
    for index, section in enumerate(sections):
        if used + len(section) > budget and kept:
            omitted = [s.splitlines()[0].lstrip("# ").strip() for s in sections[index:]]
            kept.append(f"[TRUNCATED: {len(sections) - index} section(s) not shown -- "
                        f"{'; '.join(omitted)}. The full result is in this run's "
                        "monty_result.]")
            break
        kept.append(section)
        used += len(section) + 1
    return "\n".join(kept)


def condense(value: Any, keep: int = LIST_KEEP) -> Any:
    """Every scalar and small block as it is; every long list as one line."""
    if isinstance(value, dict):
        return {k: condense(v, keep) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) <= keep:
            return [condense(v, keep) for v in value]
        numbers = [v for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numbers and len(numbers) >= len(value) // 2:
            return (f"[{len(value)} values withheld: min {min(numbers):g}, "
                    f"max {max(numbers):g}, mean {sum(numbers) / len(numbers):g}, "
                    f"{len(value) - len(numbers)} null]")
        return [condense(v, keep) for v in value[:3]] + [f"[... {len(value) - 3} more withheld]"]
    return value


def view(task: str, output: Any) -> str:
    """One successful result as Argus reads it."""
    note = ""
    renderer = RENDERERS.get(task)
    if renderer is not None and isinstance(output, dict):
        try:
            return f"(rendered by the thesis report for {task})\n" + fit_sections(renderer(output))
        except Exception as exc:                               # noqa: BLE001
            # A renderer that fails must not cost the answer its numbers.
            note = (f"(the {task} report could not be rendered: "
                    f"{type(exc).__name__}; condensed JSON follows)\n")
    text = json.dumps(condense(output), default=str)
    if len(text) > JSON_BUDGET:
        text = (text[:JSON_BUDGET] + f" [TRUNCATED: {JSON_BUDGET} of {len(text)} chars of the "
                "condensed result shown; the full result is in this run's monty_result.]")
    return note + text


def results_view(results: dict[str, Any]) -> str:
    """Every `result_N`, readable: a rendered report or condensed JSON for a
    success, the error fields whole for a failure."""
    blocks = []
    for key, result in results.items():
        task = result.get("task", "")
        head = f"#### {key}: {task} -- {result.get('status')}"
        if result.get("status") == "success":
            blocks.append(f"{head}\n{view(task, result.get('output'))}")
        else:
            failure = {k: v for k, v in result.items() if k != "output"}
            blocks.append(f"{head}\n{json.dumps(failure, default=str)[:JSON_BUDGET]}")
    return "\n\n".join(blocks)
