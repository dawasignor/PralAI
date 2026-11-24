# Workflow: `where_am_i`

What an area of interest is, from OpenStreetMap, before any analysis runs.
**Country-agnostic**: the same call works for the lower Kelani in Sri Lanka and
for Pathum Thani in Thailand (both tested 2026-10-21).
Implemented in [`core/workflows/osm_context.py`](../core/workflows/osm_context.py).
Sources: [SOURCES.md](SOURCES.md) section 8.

## Request

```json
{"event_id": 1,
 "tasks": [{"function": "where_am_i",
            "params": {"bbox": [79.94, 6.905, 80.09, 6.978]}}]}
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `bbox` | list | required | [west, south, east, north], WGS84, any country |
| `aoi_fraction` | float | 0.75 | share of the map's width and height the AOI box fills. 0.2 to 1.0 |
| `influence_km` | float | 60 | how far around the AOI dams, reservoirs, gauges and water works are searched. 0 to 150 |
| `output_dir` | str | `outputs` | map files go to `<output_dir>/<event_id or where_am_i>/` |
| `event_id` | str | `""` | names the output folder |
| `write_figure` | bool | true | false skips the map |

## Response: four sections, each fails soft

1. **`map`** -- OSM standard tiles at the smallest zoom giving a frame at least
   1,400 px wide (lowered if over 64 tiles), framed so the AOI fills
   `aoi_fraction`. `where_am_i_map.png`: AOI box in red, the cities and towns
   inside it and the water infrastructure in the frame labelled, attribution.
   `where_am_i_osm.tif`: the plain raster, EPSG:3857.
2. **`admin`** -- the administrative units the AOI lies in or crosses, and its
   **major places** (OSM `place=city|town` inside the AOI) with their units.
   Admin levels are **read per country, not assumed**: first order = level 4
   (3 where used); second order = the lowest level above that, up to 6.
   Sri Lanka: province 4, district 5. Thailand: province 4, district 6
   (municipality 7 and subdistrict 8 are not second order). Units are found
   by boundaries containing the AOI's centre or corners (`is_in`) and
   boundaries crossing it (`rel(bbox)`); the second alone misses an AOI wholly
   inside one unit. **Not reported:** the share of each unit inside the AOI,
   and minor localities (villages, hamlets).
3. **`counts`** -- buildings, hospitals, schools, commercial buildings, food
   or grocery shops, marketplaces, electric substations, each with its tag
   definition. Two group requests -- the facility counts (hospitals, schools,
   food or grocery shops, marketplaces, substations, ...) FIRST, then the
   building scans -- **sent one at a time** since 2026-11-17. They used to run
   in parallel; on the Kandy run our own buildings scan held one of
   Overpass's two slots and the facility request's retry came back 429, and
   all nine facility counts were lost. Measured 2026-10-21 in parallel:
   Kelani 51 s, Pathum Thani 116 s.

   **When a request is refused** (`_overpass`): before each retry the server's
   `/api/status` says when a slot is free and that is the wait after a 429;
   after a 502 / 504 / dropped connection the pause is at least the backoff
   floor (`RETRY_PAUSES` 15 / 45 / 90 s, +-20 % jitter), even with a slot
   free. No pause over `MAX_PAUSE_S` (90 s), at most `MAX_WAIT_S` (180 s) of
   waiting per request, `OVERPASS_ATTEMPTS` (3) attempts. **A refused group
   is retried one category at a time** (2 attempts each), so a refusal costs
   one category, not the group. No new request starts after `COUNT_BUDGET_S`
   (480 s); what was not asked is listed as not attempted.

   **Answers are stored per category and AOI** in
   `data/cache/overpass_counts/` with their retrieval time, for
   `COUNT_CACHE_DAYS` (30). A later run -- another turn, or the Module 9/10
   hazard map over the same box -- reuses them and asks only for what is
   missing; `retrieved` dates every count and `from_store` names the reused
   ones. A failure is never stored, so a gap stays `missing` (not measured,
   never 0) until a real answer arrives. A stored list located with a smaller
   cap than now asked for, while more exist, is fetched again. An uncached
   run (RQ5) reads nothing from the store but still writes to it.
4. **`influences`** -- seven water-related categories in **one** request:

| Category | Searched | OSM tags |
|---|---|---|
| Hydropower plants and dams | AOI + `influence_km` | `waterway=dam`; `power=plant` + `plant:source=hydro`; named hydro generators |
| Water supply reservoirs | AOI + `influence_km` | named `water=reservoir` or `landuse=reservoir` |
| River gauges | AOI + `influence_km` | `man_made=monitoring_station` measuring water level |
| Water treatment and intakes | AOI + `influence_km` | `man_made=water_works`, `industrial=water_treatment`, water pumping stations, wastewater plants |
| River crossings and bridges | AOI | road/rail bridge ways whose **line crosses** a river or canal line (exact intersection on geometry) |
| Floodplain industry and quarries | AOI | `landuse=industrial|quarry`, `man_made=works` |
| Flood protection | AOI | `man_made=dyke|embankment`, sluice gates, flood walls, `flood_protection=*` |

Each category gives `count`, `distinct_named`, `inside_aoi`, and up to 15
named items, nearest first, each with `distance_km` to the AOI box and
`bearing` from its centre.

## Reading it

- **Nothing is claimed to be upstream.** No river-basin dataset is wired, so
  the wide search is by distance. Over the Kelani it returns the Laxapana
  hydropower cascade (east) and also Kukule Ganga, which is in another basin
  -- distance and bearing are given so a reader can judge.
- **Everything is what OSM has mapped**, and OSM tagging is uneven: some
  floodgates are tagged `waterway=dam`, some water towers `water=reservoir`.
  Official gauge networks are often absent (Kelani: 0 gauges mapped).
- **A failed category is missing, never 0.**
- **Run time is set by Overpass** -- several minutes.
- **Tile use follows the OSM Tile Usage Policy**: identifying User-Agent, a
  tile budget, a disk cache, visible attribution.
