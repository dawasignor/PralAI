"""Flood bot mode: Module 11, the Public Report.

Needs the Flood analyst run of the same store first (D-24). Weather, the
area overview, land cover, the ranking and the flood extent are read from
that run; this pipeline adds the national context and the people.
"""

from __future__ import annotations

from .base import MissingInput, Pipeline, Run, report, tool
from .flood_analyst import flood_map_params

VOICE = ("The reader is the public: short, calm sentences, no jargon, what to do "
         "before what happened.")


def iso3(run: Run) -> str:
    code = run.settings.get("country_iso3")
    if not code:
        raise MissingInput("no country is set for this experiment")
    return code


def context_tools() -> list:
    """The steps Flood bot and Chanakya share, in one place, so the two modes
    cannot ask a tool different questions (D-3)."""
    return [
        tool("get_flood_map", flood_map_params, max_s=1800.0),
        tool("get_pop_data", lambda r: {"bbox": r.bbox, "country_iso3": iso3(r),
                                        "year": int(r.start[:4])}),
        tool("fetch_world_bank_indicators", lambda r: {"country_iso3": iso3(r)}),
        tool("fetch_inform_risk", lambda r: {"country_iso3": iso3(r), "before": r.day_before}),
        tool("search_previous_disasters", lambda r: {"country_iso3": iso3(r),
                                                     "before": r.day_before, "bbox": r.bbox}),
    ]


def build() -> Pipeline:
    return Pipeline("flood_bot", "Public report", [
        *context_tools(),
        report("public_report", "citadel", VOICE),
    ], requires_flood_analyst=True)
