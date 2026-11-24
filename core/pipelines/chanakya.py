"""Chanakya mode: Module 12, the Executive Summary.

Needs the Flood analyst run of the same store first (D-24). The Flood bot's
context tools, plus road access and the slope-and-tree check; Modules 9 and
10 of the Flood analyst run are handed to the writer to summarise for a mayor.
"""

from __future__ import annotations

from .base import Pipeline, Run, report, tool
from .flood_analyst import roads_params
from .flood_bot import context_tools

VOICE = ("The reader is a mayor: decisions first, each with its reason, in plain words; "
         "what is not known is said as not known.")


def _modules_9_10(run: Run) -> str:
    parts = []
    for slot in ("essential_services_hev", "human_health_risks_hev"):
        text = run.store.read(slot)
        if text:
            parts.append(f"### {slot.replace('_', ' ')} (the Flood analyst run)\n"
                         + text.split("\n---\n", 1)[-1][:6000])
    return "\n\n".join(parts)


def build() -> Pipeline:
    return Pipeline("chanakya", "Executive summary", [
        *context_tools(),
        tool("assess_road_access", roads_params),
        tool("check_slope_tree_roads", lambda r: {"bbox": r.bbox, **r.out()}),
        report("executive_summary", "citadel", VOICE, context=_modules_9_10),
    ], requires_flood_analyst=True)
