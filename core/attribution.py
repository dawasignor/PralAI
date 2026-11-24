"""Where the data in an answer came from, appended under it in every persona.

User decision 2026-11-01: whenever an answer rests on data fetched from an
external source, it says which source and when, in the reader's register.

    Flood bot      - OpenStreetMap, updated on 1 Nov 2026
    Chanakya,      - OpenStreetMap, (c) OpenStreetMap contributors: OpenStreetMap
    Norwood          place context, retrieved 1 Nov 2026

Written in Python after every model has finished, like the event records
(core/event_records.py), so no model can drop or reword a source. The text
comes from `core/dating.py`, the one place that names sources and dates --
dates only, no times (user decision 2026-11-01).

**What counts as used:** every tool that succeeded this turn, and every
stored payload an agent read this turn through `read_monty_payload` (a
question answered from data fetched on an earlier turn). Results computed
from other results (the ranking, the sensitivity sweep, accuracy) have no
source of their own and are left out; the data they rest on is listed when
it was fetched or read. An answer that used no data gets no block.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

from . import dating, workflows

PLAIN = "flood_bot"

# What a member of the public would call each source.
PLAIN_NAMES = {
    "lulc": "Sentinel-2 satellite images (ESA Copernicus)",
    "worldcover": "ESA WorldCover land cover map",
    "terrain": "Copernicus elevation model (GLO-30)",
    "permanent_water": "JRC Global Surface Water",
    "weather_grid": "ECMWF weather model, via Open-Meteo",
    "data_availability": "Satellite flood map catalogues (EODC, NASA)",
    "where_am_i": "OpenStreetMap",
    "population": "WorldPop",
    "world_bank_indicators": "World Bank",
    "inform_risk": "INFORM Risk Index (EU JRC)",
    "search_previous_disasters": "GDACS, Copernicus EMS, UNOSAT, World Bank",
    "map_hazard_exposure": "OpenStreetMap facilities",
}

# The payloads read from the store this turn, by store name. Per context, so
# two console runs on two threads never share a list.
_READ: ContextVar[dict[str, Any] | None] = ContextVar("attribution_read", default=None)


def begin() -> None:
    """Start a turn's record of payloads read."""
    _READ.set({})


def note_read(name: str, payload: Any) -> None:
    """Record that an agent read the stored payload `name` this turn."""
    read = _READ.get()
    if read is not None and name:
        read[workflows.cache_name(name)] = payload


# The decision matrix and the ranking (user, 2026-11-22: "the sources section
# is empty"). They acquire nothing, but they rest on two things a reader can
# check: the event values, read from the stored weather, land-cover and
# terrain results -- listed here with their own dates -- and the published
# documents behind the product scores (the payload's `sources`, S1-S11).
MCDM = {"mcdm_decision_matrix", "mcdm", "rank_flood_products"}
MCDM_INPUTS = ("weather_grid", "weather", "lulc", "worldcover", "terrain")


def _stored(name: str) -> Any:
    try:
        from . import experiment

        store = experiment.current()
        record = store.cached_input(name) if store is not None else None
    except Exception:                                          # noqa: BLE001
        return None
    return (record or {}).get("payload")


def used(monty_result: dict | None) -> list[tuple[str, Any]]:
    """(store name, payload) for every source this turn rests on, in order:
    this turn's successful fetches first, then payloads read from the store.
    A decision-matrix or ranking result brings the stored results its event
    values were read from, and stays in the list for its score sources."""
    out: dict[str, Any] = {}
    for result in ((monty_result or {}).get("results") or {}).values():
        if result.get("status") == "success" and result.get("task"):
            out[workflows.cache_name(result["task"])] = result.get("output")
    for name, payload in (_READ.get() or {}).items():
        out.setdefault(name, payload)
    if any(dating.stored_tool(n) in MCDM for n in out):
        for name in MCDM_INPUTS:
            if name not in out:
                payload = _stored(name)
                if payload is not None:
                    out[name] = payload
    return [(n, p) for n, p in out.items()
            if n not in dating.DERIVED or dating.stored_tool(n) in MCDM]


def _evidence(payload: Any, plain: bool) -> list[str]:
    """The documents behind the product scores, one line each."""
    srcs = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(srcs, dict):
        return []
    rows = []
    for key, src in srcs.items():
        if not isinstance(src, dict) or not src.get("ref"):
            continue
        if plain:
            rows.append(f"- {src['ref']}")
        else:
            url = f" {src['url']}" if src.get("url") else ""
            rows.append(f"- [{key}] {src['ref']}{url}")
    return rows


def _plain_name(name: str, payload: Any) -> str:
    if dating.stored_tool(name) in ("get_flood_map", "get_flood_extent", "flood_extent"):
        p = payload if isinstance(payload, dict) else {}
        return str(p.get("label") or p.get("product") or "flood map")
    return PLAIN_NAMES.get(name) or dating.source_line(name, payload).split(";")[0]


def _plain_date(name: str, payload: Any, zone: str | None) -> str:
    """One date, in plain words: when the data was taken, or last updated."""
    for e in dating.entries(name, payload):
        kind = e["kind"]
        if kind == "acquired":
            return f"image taken {dating.stamp(e['at'], zone)}"
        if kind == "passes":
            return f"images taken {dating.span(e['from'], e['to'], zone)}"
        if kind == "model_hours":
            return f"weather for {dating.span(e['from'], e['to'], None).replace(' (UTC)', '')}"
        if kind == "period" and e.get("from"):
            return f"map of {dating.span(e['from'], e.get('to') or e['from'], zone)}"
        if kind == "period" and e.get("note"):
            years = re.findall(r"\b(?:19|20)\d{2}\b", e["note"])
            if years:
                return f"made from data of {years[0]}" + (
                    f"-{years[-1]}" if years[-1] != years[0] else "")
    for e in dating.entries(name, payload):
        if e["kind"] == "retrieved":
            return f"updated on {dating.stamp(e['at'], zone)}"
    return "date not recorded"


def render(used_: list[tuple[str, Any]], persona: str, zone: str | None) -> str:
    """The block under the answer, or "" when the answer used no data."""
    if not used_:
        return ""
    plain = (persona or "").strip() == PLAIN
    data = [(n, p) for n, p in used_ if dating.stored_tool(n) not in MCDM]
    mcdm = [(n, p) for n, p in used_ if dating.stored_tool(n) in MCDM]
    evidence = list(dict.fromkeys(r for _, p in mcdm for r in _evidence(p, plain)))
    if plain:
        rows = [f"- {_plain_name(n, p)}, {_plain_date(n, p, zone)}" for n, p in data]
        if mcdm:
            rows.append("- Flood product scores: the products' own documents, listed below")
        out = "\n\n**Where this information comes from**\n\n" + "\n".join(dict.fromkeys(rows))
        if evidence:
            out += "\n\n**Documents behind the scores**\n\n" + "\n".join(evidence)
        return out
    rows = [f"- {dating.source_line(n, p)}: {'; '.join(dating.lines(n, p, zone))}"
            for n, p in data]
    if mcdm:
        rows.append("- Decision matrix and weights: computed from the stored results above "
                    "(the event values) and the evidence register (the scores); no "
                    "acquisition of its own")
    out = "\n\n**Sources**\n\n" + "\n".join(dict.fromkeys(rows))
    if evidence:
        out += "\n\n**Evidence behind the product scores**\n\n" + "\n".join(evidence)
    return out
