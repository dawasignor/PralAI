"""The workflows whose data most questions stand on, and whether a session has it.

Two workflows give the AI something real to comment on for most questions a
flood analyst asks: the event's weather (`fetch_weather`) and the land
cover with its index rasters (`fetch_lulc_data`). A rainfall question answered
without the weather grid, or a built-up question answered without the land
cover, is general knowledge dressed as a finding about this AOI.

So Argus is shown, every turn, which of these the session has already fetched
for its AOI and window (`SessionRecord.workflows_run`). When a question needs
one that is missing, it answers what it can, says the answer is not from this
AOI's data, and SUGGESTS the workflow as the next step -- the user decides
whether to spend a run on it. `valid_workflow` checks the suggestion: a name
must be registered, and data already fetched is never suggested again.
"""

from __future__ import annotations

from typing import Any

from . import workflows

# Order matters only for reading. `topics` is what the prompt shows Argus; the
# model reads language, the code checks names.
PREREQUISITES: list[dict[str, str]] = [
    {"workflow": "fetch_weather",
     "topics": "event weather: rainfall, storm totals, antecedent rain, "
               "cloud cover, wind",
     "label": "Fetch the event weather (rainfall, cloud, wind) for this AOI "
              "and window"},
    {"workflow": "fetch_lulc_data",
     "topics": "land use and land cover, and the index rasters: NDVI, BUI, "
               "built-up, vegetation, moisture, open water, the NDVI class map",
     "label": "Fetch the land cover and index rasters (NDVI, BUI, class map) "
              "for this AOI"},
    # Before the suitability assessment: a product can rank first on paper and
    # have no acquisition over the AOI during the event.
    {"workflow": "data_availability",
     "topics": "which of the five NRT flood products (GFM, OPERA DSWx-HLS, "
               "DSWx-S1, MODIS MCDWD, VIIRS VCDWD) actually observed the AOI "
               "in the event window -- check before ranking or choosing a "
               "flood product",
     "label": "Check which NRT flood products have data over this AOI for "
              "the event"},
]


def _registered() -> set[str]:
    try:
        return set(workflows.names())
    except Exception:                                          # noqa: BLE001
        return set()


def prompt_block(record: Any) -> str:
    """Which prerequisite data this session has, as prompt text for Argus."""
    # A record written before a rename still says the old name; it is the
    # same data (workflows.RENAMED).
    fetched = {workflows.canonical(n)
               for n in getattr(record, "workflows_run", None) or []}
    registered = _registered()
    lines = []
    for item in PREREQUISITES:
        if item["workflow"] not in registered:
            continue
        status = ("FETCHED for this AOI and window" if item["workflow"] in fetched
                  else "NOT fetched yet")
        lines.append(f"- `{item['workflow']}` -- {item['topics']} -- {status}")
    if not lines:
        return ""
    return ("## Prerequisite data\n"
            "Most questions stand on one of these. When a question is about one "
            "of these topics and its data is NOT fetched, and the user did not "
            "ask you to fetch it: answer what you can, say plainly that it is "
            "not based on this AOI's data, and set `next_step.workflow` to that "
            "workflow, with a `label` in the user's words. When it IS fetched, "
            "comment from the findings and the session digest.\n"
            + "\n".join(lines) + "\n\n")


def valid_workflow(name: str, record: Any = None,
                   also_fetched: Any = ()) -> str:
    """`name` if it is a registered workflow this session has not fetched,
    else "". Never guessed: an unknown name is dropped, not matched.

    `also_fetched` is what the current turn fetched: the record is updated
    only after the graph ends, so without it the workflow that just ran could
    be suggested as the next thing to run."""
    name = workflows.canonical((name or "").strip())
    if not name or name not in _registered():
        return ""
    fetched = {workflows.canonical(n) for n in
               [*(getattr(record, "workflows_run", None) or []), *also_fetched]}
    if name in fetched:
        return ""
    return name
