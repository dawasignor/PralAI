# Workflow: `fetch_permanent_water`

Permanent, seasonal and ephemeral surface water for a bounding box, from the
JRC Global Surface Water occurrence layer.

Implemented in [`core/workflows/water.py`](../core/workflows/water.py).
Data source and citations: [SOURCES.md](SOURCES.md).

## Why not derive it from Sentinel-2

Because it would be circular. Permanent water is a **baseline**, and a baseline
has to be independent of the event being measured. Computing it from the same
scenes that observe the flood means "water in every observation we managed to
get" -- and over a cloudy monsoon window that is three or four looks. Floodwater
present in three of three cloudy observations is indistinguishable from a river.
The baseline would absorb the very thing it exists to subtract.

JRC GSW is independent by construction: 36 years of Landsat, finished in 2021,
fixed. It returns the same answer on every run, which is what a reference layer
has to do.

`fetch_lulc_data` still returns MNDWI for **event-time** water extent. The
intended pairing is:

```
flood extent  =  (MNDWI water now)  -  (GSW occurrence >= 90)
```

## Request

```json
{"event_id": 201,
 "tasks": [
   {"function": "fetch_permanent_water",
    "params": {"bbox": [91.80, 25.50, 91.95, 25.62], "event_id": 201}}
 ]}
```

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `bbox` | list[float] | yes | | `[west, south, east, north]`, WGS84 degrees. |
| `permanent_threshold` | float | no | `90.0` | Occurrence % at or above which a pixel is permanent. |
| `seasonal_threshold` | float | no | `25.0` | Lower bound of the seasonal band. |
| `output_dir` | str | no | `outputs` | Rasters go to `<output_dir>/<event_id>/`. |
| `event_id` | int/str | no | `water` | Subdirectory name. |
| `write_rasters` | bool | no | `true` | `false` computes statistics without writing files. |

## What `occurrence` means

The band is `uint8`, 0-100: **the percentage of valid Landsat observations
between 1984 and 2021 in which that pixel was water.** 255 is fill. That
frequency is what makes "permanent" a measurable property rather than a label,
and it is why this dataset was chosen over ESA WorldCover's discrete
"permanent water bodies" class.

The 90% and 25% breakpoints are Pekel et al.'s own, not ours.

| Band | Occurrence |
|---|---|
| `permanent` | >= 90% |
| `seasonal` | 25-90% |
| `ephemeral` | > 0% and < 25% |
| `never_water` | 0% |
| `unobserved` | fill |

`unobserved` is reported separately rather than folded into `never_water`.
"Never seen as water" and "never seen" are different claims.

## Response

Live run over Shillong (201.06 km² AOI):

```
permanent    36 px    0.0252 km^2
seasonal     57 px    0.0398 km^2
ephemeral   147 px    0.1027 km^2
never_water 287760 px 201.061 km^2
unobserved    0 px
```

Plausible for the Shillong plateau, which has no large water body inside this
box. `occurrence_stats_over_water` summarises occurrence over water pixels only
-- the AOI mean is meaningless when 99.9% of the AOI sits at zero.

Raster written: `water_class.tif`, `uint8`, 1 = ephemeral, 2 = seasonal,
3 = permanent, 0 = nodata.

## Limits

- **One 10-degree tile.** Tiles are named for their north-west corner
  (`occurrence_90E_30N` covers 90-100°E, 20-30°N). An AOI spanning a boundary
  raises; mosaicking is not implemented.
- **The archive ends in 2021.** A reservoir, embankment or channel migration
  since then is not in it. For a 2026 event that is a four-year gap, and it is
  the main reason to sanity-check the permanent-water figure against MNDWI from
  a dry-season Sentinel-2 scene.
- **30 m Landsat resolution**, against the 20 m grid `fetch_lulc_data` uses.
  Narrow channels below ~30 m wide are under-represented.
