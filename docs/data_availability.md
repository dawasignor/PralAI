# data_availability

Which of the five NRT flood products observed an AOI, day by day, over an
event window. A quick glance before the suitability assessment: a product can
rank first in `rank_flood_products` and have no acquisition over the AOI during
the event.

Code: `core/workflows/availability.py`. Chart: `core/charts.py::availability`.
Tests: `tests/test_data_availability.py` (`--live` searches the real catalogues).

## Parameters

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `bbox` | list | yes | | `[west, south, east, north]`, WGS84 degrees. |
| `start_date` | str | yes | | ISO date, **local to the AOI**, inclusive. |
| `end_date` | str | yes | | ISO date, local to the AOI, inclusive. At most 62 days in the window. |
| `event_id` | str | no | `availability` | Names the output subdirectory. |
| `output_dir` | str | no | `outputs` | Where the chart is written. |
| `write_figure` | bool | no | `true` | `false` returns the counts without drawing. |
| `include_cloud` | bool | no | `true` | `false` skips the overpass-cloud lookup. |

No account, key or asset signing is needed. A run makes about a dozen public
catalogue requests and one Open-Meteo request, and costs nothing.

## Where each product is searched

| Product | API | Collection | Archive |
|---|---|---|---|
| GFM (Sentinel-1) | EODC STAC, `https://stac.eodc.eu/api/v1` | `GFM` | from 2015 |
| OPERA DSWx-HLS | NASA CMR-STAC, `https://cmr.earthdata.nasa.gov/stac/POCLOUD` | `OPERA_L3_DSWX-HLS_V1_1.0` | full |
| OPERA DSWx-S1 | NASA CMR-STAC, `.../stac/POCLOUD` | `OPERA_L3_DSWX-S1_V1_1.0` | from 2023-12-01 |
| MODIS MCDWD | NASA CMR-STAC, `.../stac/LANCEMODIS` | `MCDWD_L3_NRT_6.1` | **rolling, about one week** |
| VIIRS VCDWD | NASA CMR-STAC, `.../stac/LANCEMODIS` | `VCDWD_L3_NRT_2` | **rolling, about one week** |

The first day of each product is read from its STAC collection extent. For the
two LANCE products, the oldest granule still held is read from the NASA CMR
search API (`granules.json`, sorted by start date).

## What an instance is

**One acquisition, not one file.** A pass is cut into tiles (MGRS for OPERA,
Equi7 for GFM, 10-degree tiles for LANCE), and OPERA re-publishes reprocessed
versions under new names. Files whose sensing times fall within 10 minutes of
each other are one instance; its files, tiles and ids travel with it. On the
Kelani window, DSWx-S1 returned 10 files for one morning pass on 2 Dec 2025.

An instance means the product has a file **touching** the AOI. It does not say
how much of the AOI the file covers: an acquisition on tile 44NMN alone covers
only the eastern part of the Kelani AOI.

## Day statuses

| Status | Meaning | On the chart |
|---|---|---|
| `available` | one or more acquisitions that day; for a MODIS / VIIRS day read from GIBS, the day's composite (count 1, `source: gibs`) | filled circle, count inside |
| `none` | searched; nothing that day | grey `0` |
| `not_checkable` | no data: neither the rolling NRT archive nor GIBS holds that day | hatched grey, "no data" |
| `not_produced` | before the product's first day | pale grey, `n/a` |
| `unreachable` | the catalogue search failed (after one retry) | red hatch, `?` |

## MODIS and VIIRS: produced daily, archive rolls

MCDWD and VCDWD are NRT-only products. LANCE keeps about one week of them, in
CMR and in its own archive alike (measured 2026-10-11: the oldest granule held
was 3 Oct 2026; a Nov 2025 day returned 404 from the LANCE archive). There is
no long-term archive of either.

**A day older than that is read from the NASA GIBS archive** (BUG-030; user
decision 2026-11-12). GIBS keeps the MCDWD and VCDWD 1-Day composites for
years (MODIS from 2021, VIIRS from June 2025, with gaps listed in its
capabilities). For each such day the composite is read over the AOI:

- it holds the product and saw any ground (classes 0-3) -> `available`,
  count **1**: one daily composite. GIBS merges the day's passes, so it cannot
  say how many there were; the payload keeps `observed_fraction` (the share of
  the product's pixels that saw the ground);
- it holds the product but every pixel is 255 "insufficient data" (cloud) ->
  `none`, count **0**, `all_cloud: true` (user decision 2026-11-12: a day the
  sensor saw only cloud is not shown as a pass);
- it holds nothing over the AOI (GIBS answered 404) -> `none`, count 0;
- the read failed -> `unreachable`, `?`.

It used to be `in_gibs` with 0 passes -- a "G" cell and "0 acquisitions" in the
fact sheet -- which read as "no data" on days the archive held. Payloads stored
before 2026-11-12 still carry `in_gibs` and are drawn as before.

Only a day neither archive holds is `not_checkable` ("no data"). It is never
reported as `none`: a zero would read as "searched, nothing" when the truth is
"no record kept". Both products
are produced daily for every land tile by design, so for those days the useful
question is whether the sensor could see the ground. The chart prints the
model cloud cover at each sensor's nominal overpass under every MODIS and VIIRS
cell (`cloud_pct` in the payload):

- Terra about 10:30, Aqua about 13:30, VIIRS about 13:30 local solar time.
  Nominal: Terra and Aqua have drifted, and off-nadir views are earlier or later.
- The value is the AOI-mean cloud cover from `fetch_weather_grid` (ECMWF IFS by
  default, the model named in the request), interpolated between the two hours
  either side of the overpass. MODIS shows the lower of Terra and Aqua, because
  MCDWD combines both.
- A missing hour gives `None` (shown as `--`), never a filled value.
- It is model cloud on a ~9 km grid, not the sensor's own cloud mask.

## Days are local to the AOI

As every daily figure in this repo is. The offset is the AOI centre's civil
UTC offset from Open-Meteo's time-zone lookup (Asia/Colombo, UTC+5:30, for
Kelani); a Sentinel-1 pass at 20:00 UTC is therefore on the next local day. If
the lookup fails, the solar offset (longitude / 15) is used and
`period.offset_source` says so.

## Output

```
{"aoi": {"bbox": [...]},
 "period": {"start", "end", "days": [...], "utc_offset_h", "timezone", "offset_source"},
 "instance_definition": "...",
 "status_meaning": {...},
 "products": [{"id", "label", "sensor", "provider", "collection", "status",
               "query", "collection_start", "archive_held_from", "note",
               "days": {"2025-11-26": {"status", "count",
                                       "acquisitions": [{"utc", "local_date", "local_time",
                                                         "files", "tiles", "ids"}],
                                       "cloud_pct": {"Terra": 100.0, "Aqua": 99.9}}},
               "totals": {"instances", "days_available", "days_none",
                          "days_not_checkable", "days_not_produced"}}],
 "cloud": {"what", "overpass_local_solar_h", "model", "retrieved_at", "caveat"},
 "sources": [{"name", "url"}],
 "retrieved_at": "...",
 "figure": {"name": "data_availability", "path": ".../data_availability.png", "caption"}}
```

The chart is written to `<output_dir>/<event_id>/data_availability.png`. In a
console run it is registered as `<image - run-region-year-data_availability>`
(`core/figure_refs.register_run`), so Argus can place it in the answer.

## Kelani, 20 Nov - 5 Dec 2025 (live, 2026-10-11)

| Product | Instances | Days |
|---|---|---|
| GFM | 5 | 26 Nov (2), 1 Dec, 2 Dec (2) |
| OPERA DSWx-HLS | 8 | 7 days |
| OPERA DSWx-S1 | 4 | 26 Nov (2), 2 Dec (2) |
| MODIS MCDWD | -- | not checkable; cloud 100% at both overpasses 24-28 Nov |
| VIIRS VCDWD | -- | not checkable; same |

## Known limits

- Footprint coverage of the AOI is not measured; an instance may cover part of it.
- An instance says a file exists, not that it is usable: the optical products
  (DSWx-HLS, MCDWD, VCDWD) may be mostly cloud. Only MCDWD and VCDWD carry a
  cloud figure here.
- Logos are text labels for now.
