"""The lay of the land for a place name: where it is, what is in it, what hit it.

Every other workflow in this package takes a bounding box as given. This is the
one that produces it, in the order the answer is actually built:

  1. **Where.** geopy against OpenStreetMap Nominatim -- the bounding box and
     the region it sits in. This is the step the rest of the run depends on,
     so it runs first and alone.
  2. **What is in it.** Overpass, for counts of mapped buildings, homes,
     apartments, commercial premises, hospitals and schools inside that box.
     Exposure context: a flood over 40,000 buildings is a different problem
     from the same flood over 400.
  3. **What hit it.** GDACS, for a recorded event matching the dates. This is
     last and it is optional, and that ordering is the point -- see below.

**Why the event comes last.** Tagging the AOI to a disaster register entry used
to happen on the way to the bounding box, and a register that answered with
nothing, or with two equally good candidates, took the bounding box down with
it. The geometry does not depend on the event: Shillong has the same extent
whether or not GDACS recorded a cyclone that week. So the lookups are ordered
by what depends on what, and step 3 failing leaves steps 1 and 2 standing.

Both providers are the ones already used in `core/places.py`, which owns every
HTTP call here. Nothing in this module talks to a network directly.
"""

from __future__ import annotations

from typing import Any

from .. import places
from . import retired


# RETIRED from the registry on 2026-10-25: the
# place name becomes a bounding box in the resolver (core/resolve.py, through
# core/places.py), the event lookup is core/event_records.py, and the counts
# are where_am_i's -- houses and apartments included. Verified before
# retiring: every count both tools measured was identical on Derna and on
# kelani-lower-2025. This file is kept until the user approves its deletion.
@retired("fetch_place_context", "where_am_i",
         "the place name is resolved before routing, and where_am_i counts "
         "the buildings, houses, apartments and facilities")
def fetch_place_context(
    place: str,
    start_date: str = "",
    end_date: str = "",
    hazards: str = "",
    include_features: bool = True,
    limit: int = 5,
) -> dict[str, Any]:
    """Resolve a place name to an AOI, its built environment, and its event.

    Args:
        place: the name to resolve -- "Derna, Libya", "Shillong", "Boston".
        start_date, end_date: ISO dates bounding the event search. Both empty
            skips step 3 entirely; a register lookup with no window would
            return whatever happened to be recent.
        hazards: comma-separated GDACS codes from `places.HAZARDS` ("FL,TC").
            Empty means every hazard, which is the honest default -- a flood
            week is legitimately answered by the cyclone that caused it.
        include_features: False skips the Overpass call. Use it when only the
            geometry is wanted; the feature count is the slow step by far.
        limit: how many gazetteer candidates to return.

    Returns `aoi`, `candidates`, `exposure`, `event` and `sources`. Every
    section carries its own `ok` and `problem`, and a section that failed is
    reported as failed rather than omitted -- "no hospitals mapped" and "the
    query timed out" are different facts and must not arrive looking alike.
    """
    # -- 1. where ----------------------------------------------------------
    found = places.geocode_place(place, limit=limit)
    candidates = found["candidates"]
    if not candidates:
        # No bounding box means there is nothing for steps 2 and 3 to be about.
        # This is the one failure that stops the workflow.
        raise ValueError(
            f"no place matched {place!r} in the OpenStreetMap gazetteer"
            + (f" ({found['problem']})" if found["problem"] else ""))

    top = candidates[0]
    bbox = top.get("bbox")
    aoi = {
        "query": place,
        "display": top.get("display", ""),
        "bbox": list(bbox) if bbox else None,
        "centroid": [top.get("lon"), top.get("lat")],
        "country": top.get("country", ""),
        "admin": top.get("admin", ""),
        "kind": top.get("kind", ""),
        "area_km2": round(places.bbox_km2(bbox), 1) if bbox else None,
    }

    # Ambiguity is reported, not resolved. Choosing between two Bostons is a
    # question for the user, and `core/resolve.py` is where that conversation
    # already lives -- a workflow silently picking the top hit is how an
    # analysis ends up on the wrong continent with nobody able to see it.
    rest = [{"display": c.get("display", ""), "country": c.get("country", ""),
             "admin": c.get("admin", ""), "bbox": c.get("bbox")}
            for c in candidates[1:]]

    # -- 2. what is in it --------------------------------------------------
    if include_features and bbox:
        exposure = places.osm_features(bbox)
    elif not bbox:
        exposure = {"ok": False, "counts": {}, "source": places.OVERPASS_SOURCE,
                    "problem": "the gazetteer returned no bounding box"}
    else:
        exposure = {"ok": False, "counts": {}, "source": places.OVERPASS_SOURCE,
                    "problem": "skipped (include_features=False)"}

    # -- 3. what hit it ----------------------------------------------------
    #
    # Last, and allowed to fail. GDACS indexes by country, not by city, so the
    # search uses what the gazetteer returned rather than what the user typed.
    if start_date and end_date and aoi["country"]:
        event = places.find_event(aoi["country"], start_date, end_date, hazards)
    else:
        missing = ("no country from the gazetteer" if not aoi["country"]
                   else "no date range given")
        event = {"ok": False, "events": [], "source": places.GDACS_SOURCE,
                 "problem": f"event search skipped -- {missing}"}

    return {
        "aoi": aoi,
        "candidates": rest,
        "ambiguous": bool(rest),
        "exposure": exposure,
        "event": event,
        "sources": [places.OSM_SOURCE, places.OVERPASS_SOURCE,
                    places.GDACS_SOURCE],
    }
