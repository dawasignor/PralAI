"""The thesis path: the outputs of the thesis, in order, and how far a session is.

The console draws this as a flowchart in place of the workflow list, and Argus
is shown it every turn so it can recommend the next step. One definition
serves both, so the chart and the recommendation cannot disagree about what
comes next.

    Where am I  (first in order; nothing waits on it)

    Weather (2)  Land cover  Terrain  JRC water bodies  Availability (1)
                      \\        |      /                   |
                       AOI brief (3)       Scores and weights (6) -> Ranking (7)
                                       /          |             |         \\
                          Sensitivity (8)  Essential (9)  Health (10)  Accuracy

A step is **done** when this session has run it for its current AOI and window:
a workflow is in `SessionRecord.workflows_run`, a flow in `flows_run`. Both are
cleared when the AOI or the window changes, so a step done for another place
is not done here. The **next** step is the first one, in the order below, that
is not done and whose `needs` are all done. Everything else is **todo**.

A workflow step whose function is not registered is left out, not shown as
todo: `accuracy_metrics` lives on one branch only, and a node that can never
run is a promise the console cannot keep.

The recommendation is an OFFER. Argus is asked to make it; when it does not,
the graph offers the path's next step itself (`suggestion`), logged as such.
Nothing on this path is ever run without the user asking.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import one_liners, workflows

# Order is the order of the thesis; `needs` is what a step stands on. `prompt`
# is what the console sends if the user accepts the suggestion, so it is
# written as a request Argus can route without further context.
STEPS: list[dict[str, Any]] = [
    # Where the AOI is and what is on it, before anything about the flood
    # (user decision 2026-11-01). It needs nothing and nothing waits on it;
    # it is first only in the order, so a fresh session is offered it first.
    {"id": "where", "label": "Where am I", "output": "",
     "kind": "workflow", "name": "where_am_i", "needs": [],
     "prompt": "Where am I? Describe the target area",
     "why": "The place itself: the administrative area, the towns, and the "
            "land use and features OpenStreetMap records there."},
    # Module 1 runs first of the analysis: a product
    # can rank first on paper and have no acquisition over the AOI.
    {"id": "availability", "label": "Availability", "output": "Module 1",
     "kind": "workflow", "name": "data_availability", "needs": [],
     "prompt": "Check which NRT flood products observed this AOI during the "
               "event",
     "why": "Module 1: which products have any acquisition over the AOI in the "
            "window -- checked before anything is ranked."},
    {"id": "weather", "label": "Weather", "output": "Module 2",
     "kind": "workflow", "name": "fetch_weather", "needs": [],
     "prompt": "Fetch the event weather for this AOI and window and show the "
               "rainfall hyetograph",
     "why": "Module 2: the storm totals, peaks and antecedent rain the flood "
            "stands on."},
    # `or`: WorldCover stands in when Sentinel-2 failed its threshold
    #. Whether the fallback is admissible -- a recorded
    # failure at the defaults -- is checked where the indices are computed
    # (fact_sheet, mcdm.s2_failed_at_defaults), not here.
    {"id": "lulc", "label": "Land cover", "output": "Module 3",
     "kind": "workflow", "name": "fetch_lulc_data", "or": ["fetch_worldcover"],
     "needs": [],
     "prompt": "Fetch the land cover and index rasters (NDVI, BUI, class map) "
               "for this AOI",
     "why": "Module 3: vegetation and built-up shares, for the AOI brief and the "
            "ranking."},
    {"id": "terrain", "label": "Terrain", "output": "Module 4",
     "kind": "workflow", "name": "fetch_terrain_data", "needs": [],
     "prompt": "Fetch the terrain (elevation and slope) for this AOI",
     "why": "Module 4: slope classes, for the AOI brief and the ranking."},
    {"id": "water", "label": "JRC water bodies", "output": "Module 5",
     "kind": "workflow", "name": "fetch_permanent_water", "needs": [],
     "prompt": "Fetch the permanent and seasonal surface water baseline for "
               "this AOI",
     "why": "Module 5: the normally-wet baseline a flood map is read against."},
    {"id": "brief", "label": "AOI brief", "output": "Module 3",
     "kind": "flow", "name": "norwood:pre_flood_aoi_brief",
     "needs": ["lulc", "terrain", "water"],
     "prompt": "Write the pre-flood AOI brief from the land cover, terrain and "
               "permanent water data",
     "why": "Module 3: what the AOI looked like before the flood, and how a "
            "flood map over it is likely to behave."},
    # Modules 6 and 7 are two steps (user decision 2026-11-19): the scores and
    # weights first, the ranking on the next turn -- where the user may change
    # any weight. A ranking run carries the same matrix, so it counts as
    # Module 6 too (`or`).
    {"id": "matrix", "label": "Scores and weights", "output": "Module 6",
     "kind": "workflow", "name": "mcdm_decision_matrix", "or": ["rank_flood_products"],
     "needs": ["availability", "weather", "lulc", "terrain"],
     "prompt": "Show the decision matrix: each flood product's scores and each "
               "criterion's weight for this event",
     "why": "Module 6: what each product scores on each criterion, and how much "
            "each criterion counts for this event -- before anything is ranked."},
    {"id": "ranking", "label": "Ranking", "output": "Module 7",
     "kind": "workflow", "name": "rank_flood_products",
     "needs": ["matrix"],
     "prompt": "Rank the five NRT flood products with TOPSIS using these weights",
     "why": "Module 7: the TOPSIS ranking from the Module 6 scores and weights; "
            "any weight can be changed first by stating it."},
    {"id": "sensitivity", "label": "Sensitivity", "output": "Module 8",
     "kind": "workflow", "name": "sensitivity_analysis", "needs": ["ranking"],
     "prompt": "Run the weight sensitivity analysis on the product ranking",
     "why": "Module 8: how far each weight can move before the ranking changes."},
    # Modules 9 and 10 (user decision 2026-11-02): Chanakya flows that write
    # the module -- the flood map of the best-ranked product, the people in
    # it, INFORM, then the risks and the actions (core/pipelines/hev.py).
    {"id": "essential", "label": "Essential services", "output": "Module 9",
     "kind": "flow", "name": "citadel:essential_services_hev", "needs": ["ranking"],
     "prompt": "What were the risks to essential services in this flood event?",
     "why": "Module 9: hospitals, drinking water and food outlets in the flood, the "
            "people affected, and what to do."},
    {"id": "health", "label": "Health risks", "output": "Module 10",
     "kind": "flow", "name": "citadel:human_health_risks_hev", "needs": ["ranking"],
     "prompt": "Make a health risk report for this flood event.",
     "why": "Module 10: sewage, drinking water, mosquitoes and mould -- the health "
            "risks of the flood, and what to do."},
    {"id": "accuracy", "label": "Accuracy", "output": "",
     "kind": "workflow", "name": "accuracy_metrics", "needs": ["ranking"],
     "prompt": "Compute the agreement of the NRT flood products with the "
               "reference flood map",
     "why": "Agreement with the reference, set beside the TOPSIS rank."},
]


def _registered() -> set[str]:
    try:
        return set(workflows.names())
    except Exception:                                          # noqa: BLE001
        return set()


def _steps() -> list[dict[str, Any]]:
    """STEPS without the workflows this checkout does not register.

    A dropped step is also dropped from every `needs` list, so nothing waits
    on a node that is not there.
    """
    registered = _registered()
    kept = [s for s in STEPS if s["kind"] != "workflow" or s["name"] in registered]
    ids = {s["id"] for s in kept}
    return [{**s, "needs": [n for n in s["needs"] if n in ids]} for s in kept]


def view(record: Any = None, *, extra_workflows: Iterable[str] = (),
         extra_flows: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Every step with its status: "done", "next" or "todo".

    `extra_*` add what THIS turn ran: the session record is updated only after
    the graph ends, so while Argus writes its answer the fetch it is answering
    about is not in the record yet.
    """
    # Old names in a record (workflows.RENAMED) count as the tool they became.
    fetched = {workflows.canonical(n) for n in
               [*(getattr(record, "workflows_run", None) or []), *extra_workflows]}
    flows = set(getattr(record, "flows_run", None) or []) | set(extra_flows)
    steps = _steps()
    done = {s["id"] for s in steps
            if (({s["name"], *s.get("or", [])} & fetched) if s["kind"] == "workflow"
                else s["name"] in flows)}

    out, chosen = [], False
    for s in steps:
        if s["id"] in done:
            status = "done"
        elif not chosen and all(n in done for n in s["needs"]):
            status, chosen = "next", True
        else:
            status = "todo"
        out.append({"id": s["id"], "label": s["label"], "output": s["output"],
                    "kind": s["kind"], "name": s["name"], "needs": s["needs"],
                    "status": status, "prompt": s["prompt"], "why": s["why"],
                    # The console's Tools and workflows graph: the plain line
                    # shown on click, and the agent the step hangs off.
                    "summary": one_liners.WORKFLOWS.get(s["id"], ""),
                    "owner": one_liners.owner(s["id"])})
    return out


def next_step(record: Any = None, **extra: Iterable[str]) -> dict[str, Any] | None:
    """The step the path recommends now, or None when every step is done."""
    return next((s for s in view(record, **extra) if s["status"] == "next"), None)


def suggestion(record: Any = None, **extra: Iterable[str]) -> dict[str, Any] | None:
    """The next step as a `next_step` offer, for when Argus made none.

    None without an AOI and a window: every step runs over both, and offering
    a fetch the run would have to stop and ask about is not a next step.
    """
    if record is None or not (record.place.bbox and record.window.start):
        return None
    step = next_step(record, **extra)
    if step is None:
        return None
    return {
        "label": step["prompt"],
        "flow": step["name"].split(":", 1)[1] if step["kind"] == "flow" else "",
        "workflow": step["name"] if step["kind"] == "workflow" else "",
        "why": f"Next on the thesis path. {step['why']}",
    }


def prompt_block(record: Any = None, **extra: Iterable[str]) -> str:
    """The path and its status, as prompt text for Argus."""
    steps = view(record, **extra)
    if not steps:
        return ""
    mark = {"done": "done", "next": "NEXT", "todo": "todo"}
    lines = []
    for s in steps:
        what = (f"workflow `{s['name']}`" if s["kind"] == "workflow"
                else f"flow `{s['name'].split(':', 1)[1]}` ({s['name'].split(':', 1)[0]})")
        out = f" ({s['output']})" if s["output"] else ""
        lines.append(f"- [{mark[s['status']]}] {s['label']}{out} -- {what}")
    nxt = next((s for s in steps if s["status"] == "next"), None)
    tail = (f"The next step is **{nxt['label']}**. When you finish, recommend it "
            "as `next_step` -- `label` in the user's words, and `workflow` or "
            "`flow` set to the name above -- unless the user's question clearly "
            "calls for a different follow-up. It is offered, never run."
            if nxt else
            "Every step on the path is done for this AOI and window.")
    return ("## Thesis path\n"
            "The outputs of the user's thesis, in order, with their status for "
            "this AOI and window. The console draws this as a flowchart.\n"
            + "\n".join(lines) + "\n\n" + tail + "\n\n")
