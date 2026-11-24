# Workflow: `fetch_place_context`

Turns a place name into a bounding box, the built environment inside it, and
the disaster record that matches it.

Implemented in [`core/workflows/place.py`](../core/workflows/place.py); every
HTTP call it makes lives in [`core/places.py`](../core/places.py).
Data sources and citations: [SOURCES.md](SOURCES.md).

This is the workflow the others depend on. `fetch_lulc_data`,
`fetch_terrain_data` and `fetch_permanent_water` all take a `bbox` as given.
This is where that `bbox` comes from.

## The order of the three lookups is the design

```
1. geopy / Nominatim   ->  bounding box + region        (required)
2. Overpass            ->  what is built inside it      (optional)
3. GDACS               ->  which recorded event it was  (optional)
```

Step 1 runs first and alone because everything else is about the box it
returns. Steps 2 and 3 are allowed to fail without taking the result down.

**This ordering is a bug fix, not a preference.** Tagging the AOI to a GDACS
entry used to happen on the way to the bounding box, and a register that
answered with nothing -- or with two equally good candidates -- took the
bounding box with it. The geometry never depended on the event: Shillong has
the same extent whether or not GDACS recorded a cyclone that week. Ordering the
lookups by what actually depends on what is what stops a register outage from
costing an AOI. See `core/resolve.py` for the same rule on the conversational
path.

## Request

```json
{"event_id": 170,
 "tasks": [
   {"function": "fetch_place_context",
    "params": {"place": "Derna, Libya",
               "start_date": "2023-09-01",
               "end_date": "2023-09-20"}}
 ]}
```

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `place` | str | yes | | Name to resolve. A country qualifier removes most ambiguity. |
| `start_date` | str | no | `""` | ISO date. Both dates empty skips step 3. |
| `end_date` | str | no | `""` | ISO date. |
| `hazards` | str | no | `""` | Comma-separated GDACS codes (`FL,TC`). Empty means every hazard. |
| `include_features` | bool | no | `true` | `false` skips Overpass -- by far the slow step. |
| `limit` | int | no | `5` | Gazetteer candidates to consider. |

## Response

```json
{"aoi": {"display": "Derna, Libya",
         "bbox": [22.5568, 32.7401, 22.6843, 32.7915],
         "centroid": [22.6383, 32.7594],
         "country": "Libya", "admin": "Derna", "kind": "city",
         "area_km2": 67.8},
 "candidates": [{"display": "Derna, Libya", "bbox": [21.92, 30.79, 23.48, 33.14]}],
 "ambiguous": true,
 "exposure": {"ok": true,
              "counts": {"buildings": 14665, "houses": 0, "apartments": 2930,
                         "commercial": 1, "hospitals": 7, "schools": 25},
              "missing": [], "area_km2": 67.8, "problem": ""},
 "event": {"ok": true,
           "events": [{"name": "Flood in Libya", "kind": "flood",
                       "start": "2023-09-08", "end": "2023-09-14",
                       "glide": "FL-2023-000170-LBY", "severity": "Red"}]},
 "sources": ["OpenStreetMap Nominatim (community gazetteer)",
             "OpenStreetMap Overpass API (community survey data)",
             "GDACS (JRC / UN OCHA)"]}
```

Every section carries its own `ok` and `problem`. A section that failed is
reported as failed rather than omitted.

### `ambiguous` is not resolved here

More than one plausible place comes back as `ambiguous: true` with the rest in
`candidates`. Choosing between two Bostons is a question for the user, and
`core/resolve.py` is where that conversation already lives. A workflow that
silently took the top hit is how an analysis ends up on the wrong continent
with nobody able to see it.

## Reading the exposure counts

**These are counts of *mapped* features, not counts of features.** OSM coverage
is excellent in some cities and thin in others. A low count can mean few
buildings or few surveyors, and nothing in the response distinguishes them.
Never report one as a population, an asset total, or a damage denominator
without saying where it came from.

`houses: 0` above is the honest illustration: Derna's residential buildings are
tagged `apartments`, not `house`. The zero is real and it is not "no homes".

A category that failed is **absent** from `counts` and listed in `missing`. It
is never present as `0` -- zero means "none mapped", which is a finding;
a timeout means "not known", which is not.

## Cost and failure modes

- **Overpass is the slow step**, roughly a minute for six categories over a
  city, because the requests are deliberately paced. Pass
  `include_features: false` when only the geometry is wanted.
- **The exposure step is bounded at 300 s total** (`deadline_s` in
  `places.osm_features`). Six categories retried with nothing stopping them is
  twenty minutes, and nothing above a workflow interrupts one --
  `core/monty_graph.py` runs a task to completion with no wall clock around it.
  Categories past the budget land in `missing` as "not attempted".
- **One endpoint, tried twice.** 429 and 504 from the public instance are load,
  and load passes, so a refusal waits `BUSY_PAUSE` and asks again.

### Do not add a second Overpass endpoint without measuring

Two were tried and both made it worse.

`overpass.kumi.systems` is global, legitimate, and did not answer from this
network at all. Every fallback to it spent the pause plus the full read timeout
and returned nothing -- and it spent that from the same budget the remaining
categories needed. Six categories became one. **A fallback that never answers
is not redundancy; it is a tax on every failure.**

`overpass.osm.ch` was worse, and is its own section below.

### Never add a regional Overpass instance

Every endpoint in `places.OVERPASS_URLS` must carry the whole planet. A
regional instance does not fail on an AOI it has no data for -- it answers
`200` with a count of `0`, which arrives as the finding "nothing mapped here"
with nothing to mark it false. That is strictly worse than a timeout, because
a timeout is visible.

`overpass.osm.ch` was in the list and is the worked example. Same bbox, same
query, on the same afternoon:

| Endpoint | Derna | Zurich |
|---|---|---|
| `overpass-api.de` | 14,657 | 21,780 |
| `overpass.osm.ch` | **0** | 21,780 |

It looked healthy in every respect except the number. Across three runs it
zeroed a different subset of categories each time -- whichever ones happened to
fall through to it -- and every one of those runs reported `missing: []` and
`problem: ""`. One of them returned `buildings: 0`, the denominator the other
five are read against, for a city with 14,665 of them.

That is the shape of the hazard: not a mirror that is wrong, but a mirror that
is wrong *intermittently and invisibly*, depending on which server answered
first. Before adding an endpoint, run the two-AOI check against it: one AOI
inside its likely region and one far outside. The far one must not come back
`0`.
- **AOIs over `MAX_AOI_KM2` (5,000 km2) are refused** for the feature count,
  with the area in the `problem`. A province-sized box is a query that times
  out rather than one that answers slowly.
- **No place match raises.** It is the one failure that stops the workflow:
  with no bounding box there is nothing for steps 2 and 3 to be about.

## See also

- [WORKFLOWS.md](WORKFLOWS.md) -- the full catalogue.
- `core/resolve.py` -- the conversational path that settles an AOI and window
  by asking, and the matching guard that stops an event answer from clobbering
  a resolved AOI.
