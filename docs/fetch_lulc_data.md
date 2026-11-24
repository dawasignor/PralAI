# Workflow: `fetch_lulc_data`

Sentinel-2 land-cover indices for a bounding box, taken from the most recent
usable scene in a lookback window that ends at `start_date`.

Implemented in [`core/workflows/sentinel.py`](../core/workflows/sentinel.py).
Data sources and citations: [SOURCES.md](SOURCES.md).

## Request

```json
{"event_id": 404,
 "tasks": [
   {"function": "fetch_lulc_data",
    "params": {"bbox": [91.80, 25.50, 91.95, 25.62],
               "start_date": "2025-08-03",
               "event_id": 404}}
 ]}
```

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `bbox` | list[float] | yes | | `[west, south, east, north]`, WGS84 degrees. |
| `start_date` | str | yes | | `YYYY-MM-DD`. The lookback window **ends** here. |
| `end_date` | str | no | `None` | Echoed only. Does not widen the search. |
| `lookback_days` | int | no | `90` | How far back to walk for a usable scene. |
| `min_valid_fraction` | float | no | `0.7` | Share of AOI pixels that must be real surface observation. See below. |
| `max_scene_cloud` | float | no | `99.0` | Tile-level prefilter, applied before any pixel is read. |
| `max_probes` | int | no | `12` | Cap on SCL reads, so a fully clouded season stays bounded. |
| `probe_decimation` | int | no | `8` | Probes read SCL at 1/N through the COG overviews. `1` disables. See below. |
| `min_aoi_coverage` | float | no | `1.0` | Share of the AOI a scene must actually cover. See below. |
| `mosaic` | bool | no | `true` | When no single scene covers the AOI, paste same-date neighbouring tiles. See below. |
| `indices` | list[str] | no | all six | Any of `ndvi`, `ndbi`, `bui`, `ndmi`, `nmdi`, `mndwi`. |
| `output_dir` | str | no | `outputs` | Rasters go to `<output_dir>/<event_id>/`. |
| `event_id` | int/str | no | scene id | Subdirectory name. |
| `write_rasters` | bool | no | `true` | `false` computes statistics without writing files. |

The window is `[start_date - lookback_days, start_date]`. It ends at
`start_date` on purpose: this is the **pre-event baseline**, so no imagery from
during the flood is used to describe the land cover the flood arrived on.

## Scene selection

This is the part that carries the design, and it exists because of a measured
problem. A flood is a cloudy-season event, so the pre-event window is the worst
window Sentinel-2 has. Measured over the Shillong AOI:

| Window before 2025-08-03 | Scenes | Tile cloud < 20% |
|---|---|---|
| 15 days | 5 | 0 |
| 90 days | 25 | 1 |
| Nov-Dec (dry season) | 16 | 13 |

So the workflow does not composite and does not take the clearest scene. It
walks backwards from `start_date` and takes the **most recent scene that is
actually usable over the AOI**. Recency wins because land cover is a baseline:
a scene from three weeks ago describes the catchment better than a cleaner one
from three months ago.

### Why the bar is 0.70

`min_valid_fraction` defaults to **0.7**. It has been 0.7, then 0.9, then 0.8,
and now 0.7 again, and the history is worth stating because two of those moves
were decided on a measurement that was wrong.

The 0.9 came from a de-spiking convention -- retaining only observations at
>= 0.90 valid fraction (Martins Jr et al., SSRN doi:10.2139/ssrn.7226312, see
`docs/SOURCES.md` section 6). That paper is **Sentinel-1 SAR**, benchmarking a
VV Otsu threshold and a VV+VH SVM against JRC Global Surface Water. This
workflow is optical Sentinel-2 and its valid fraction is an SCL cloud mask, not
a water-detection quality flag. The 0.90 was therefore adopted as a
**quality-retention convention, not a transferred measurement** -- say so when
citing it. The argument for a high bar is that this output is not read and
judged by a person, it feeds the MCDM score, and an unobserved fraction becomes
bias in a decision rather than a caveat in a paragraph.

**Then the measurements it was being weighed against turned out to be false.**
Until the `plan_grid` clamp was fixed (2026-09-22), an AOI overhanging a tile's
western edge had its window *translated* east rather than truncated: the read
kept its full pixel count and slid to a region the caller never asked for, so
`coverage` computed 1.0 and the AOI gate waved it through. Over the Kelani
basin that moved the window 1,258 px -- 25 km -- onto drier ground, and the
valid fractions it reported were of that other place.

| Kelani (original AOI), 2025-10-31 | reported | true |
|---|---|---|
| single scene, its own extent | 0.8515 | **0.7287** over 0.904 of the AOI |
| two-tile mosaic, whole AOI | -- | **0.7357** |

So the Kelani baseline never cleared 0.9 and does not clear 0.8. Nothing in a
90-day lookback exceeds 0.7357; the next best date is 0.3926. Sweeping 900 days
back, the first mosaic to clear 0.9 is 2025-03-20 at 0.9011 -- 249 days before
the event, in the opposite season -- and 0.8 and 0.85 select that same scene,
so every threshold above 0.74 costs eight months of recency and buys nothing
over each other.

On the extended AOI (west edge 79.800, sea excluded) the same 2025-10-31 mosaic
gives **0.7416 of the land**: slightly clearer, because the coastal strip that
was added is less cloudy than the uplands. **0.7 is not a
threshold set to admit a marginal scene** -- below it this catchment has no
pre-event Sentinel-2 observation at all, and the choice is between a baseline
labelled as 74% observed and no baseline.

**Selection is judged on the land, like the reported figure (since
2026-09-26).** Until then the threshold was applied to the valid fraction of
the whole window, sea included, while `scene.aoi_valid_fraction` was a share of
the land. A usually-clear sea voted on whether a scene passed: Kelani-west was
selected on 0.7777 and reports 0.7416, and Bangkok on 0.8686 and reports 0.8243.
`select_scene` and `select_mosaic` now take the land mask and measure both the
probe and the confirmation over it, so the accepted figure IS the reported one.
Re-run on both events, neither selection changed -- Bangkok's 18 Aug mosaic,
the one that might have, is 0.6293 of its land. Every attempt records
`valid_fraction_is_of`; cached payloads fetched before the fix hold
window-based attempt figures and say nothing, so read them as such.

**Any Kelani valid fraction quoted above 0.74 predates the clamp fix and is
void.** The cost of 0.7 is that up to 30% of an AOI can be unobserved behind a
figure that still reads like a whole-AOI mean, which is why `valid_fraction`
travels with every statistic and why the consuming brief reports it as an
uncertainty section rather than a footnote.

Every rejection is recorded and returned in `selection.attempts`. That is
provenance: it is how you know *why* a 3-month-old scene was used.

If nothing in the window qualifies, the task **fails** with the best candidate
named, rather than returning a number computed from cloud:

```
no Sentinel-2 scene reaches valid fraction 0.7 over the AOI within the lookback
window (12 probed; best was S2C_T46RCP_20250510T043037_L2A at 0.6209 on
2025-05-10). Lower min_valid_fraction or raise lookback_days.
```

### Probes are cheap; the accepted scene is not

A probe answers one question -- roughly what share of this AOI is cloud -- and
that does not need 20 m pixels. Sentinel-2 SCL COGs carry overviews at 2, 4, 8
and 16, so `probe_decimation` (default 8) reads the same window at 1/8 the
resolution.

Measured over six live Shillong scenes: **5.1x faster**, worst-case valid
fraction error **0.008**. Against a 0.7 threshold that error is immaterial.

**The estimate only decides whether a scene is worth confirming.** As soon as a
scene clears the threshold on the cheap probe it is re-read at full resolution,
and it is the full-resolution number that is compared against
`min_valid_fraction` and returned. So nothing that leaves the workflow is an
estimate:

- `selection.attempts[].estimated` -- `true` on scenes rejected from the cheap
  probe alone, `false` on any scene that was read in full.
- `selection.attempts[].probe_valid_fraction` -- the estimate that earned a
  confirmation read, kept beside the measurement it was checked against.
- `scene.aoi_valid_fraction`, `scl_counts` and every index statistic are always
  measured at full resolution.

A scene can pass the probe and then fail confirmation. That is not an error --
the threshold is on the measurement, never on the estimate -- and the near miss
is recorded with both numbers rather than looking like a probe that never ran.

Live A/B at the 0.9 default, `probe_decimation` 1 against 8:

| AOI | Full-res | 1/8 | Scene chosen | valid_fraction |
|---|---|---|---|---|
| Edinburgh | 2.66 s | **0.53 s** | identical | 0.9709 both |
| Shillong (fails) | 10.38 s | **3.10 s** | raises both | -- |

### One grid per MGRS tile, not one per scene

Sentinel-2 products sit on a fixed worldwide MGRS grid, so every scene of one
tile shares an extent, a CRS and a pixel alignment. The AOI window is therefore
identical across dates, and computing it per candidate means re-deriving a known
answer -- one COG open each time. Over Shillong, 13 of 13 candidates were
`MGRS-46RCP`: the same window, computed 13 times.

The window is now cached per tile for the life of the search, keyed on the STAC
`grid:code` property, falling back to `mgrs:utm_zone` + `mgrs:latitude_band` +
`mgrs:grid_square`.

**A scene the catalogue does not identify computes its own grid.** The tile key
is `None` rather than a guess in that case, and a candidate from a different
tile is a cache miss, so neither borrows a neighbour's window. This matters for
an AOI near a tile boundary, where the search genuinely returns scenes from
several tiles: a borrowed window would read the wrong pixels and still return an
array of the right shape, so nothing downstream would notice.

### A scene must cover the whole AOI

Sentinel-2 scenes are MGRS tiles, and an AOI can straddle two of them. The
window is clamped to the raster it is read from, so a scene covering half the
AOI would otherwise return perfectly plausible statistics for **half the area
that was requested**, with nothing in the response saying so.

So coverage is measured before the SCL is read, and a scene below
`min_aoi_coverage` (default `1.0`) is rejected the same way a cloudy one is --
recorded in `selection.attempts` with an `aoi_coverage` figure. Rejection
rather than a hard raise, because a scene from an adjacent orbit may cover the
AOI in full.

If every candidate is rejected for coverage the failure is a typed one,
`NoCoveringScene`, and it says so rather than blaming cloud -- the two call for
different fixes, and one of them the code can perform itself.

`aoi.bbox_requested`, `aoi.bbox_covered` and `aoi.coverage` are returned on
every run, not only partial ones, so `area_km2` can never be read without the
figure that qualifies it.

### Mosaicking across MGRS tiles

`mosaic` (default `true`) is the answer to `NoCoveringScene`. A river basin is
not obliged to fit inside one 110 km tile: the Kelani crosses the T44NLN /
T44NMN boundary, so **every** scene covers part of it and none covers all of
it. `select_mosaic` groups the candidates by acquisition **date**, one tile per
group, and pastes the group onto a single grid.

**It is a fallback, never a first choice.** `select_scene` runs first and a
single scene always wins when one qualifies: it is one acquisition, one sun
angle, no seam. The fallback is reached only on the typed exception, so a
threshold the user set ("nothing was clear enough") is never worked around by
code meant for a tile boundary ("nothing was big enough").

**A mosaic is not automatically one observation, so it says which it is --
and the field that says so is `scene.single_datatake`.** One datatake is one
continuous acquisition by one satellite on one orbit, so scenes sharing it are
the same pass cut at a tile boundary.

**Do not use the timestamp span for this.** Every tile product carries the time
of its own cut, so two tiles of a single pass are seconds apart as a matter of
course -- the Kelani pair is 3.4 s apart and is one pass. An earlier version of
this block tested `acquisition_span_seconds == 0` and consequently reported a
single pass as "several observations pasted together"; the consuming agent
believed it, as it should. `acquisition_span_seconds` is still reported, as a
magnitude. `scene.datatakes` lists the ids, `scene.acquisition_note` states the
conclusion in words, and a datatake the catalogue omits yields "cannot be
established" rather than a guess.

`scene.datetime` is the **oldest** member, because a baseline is only as recent
as its stalest part, with both ends in `scene.datetime_range`.

Measured over the original Kelani AOI (west edge 79.867, 1289.16 km2),
2025-10-31. **That AOI was superseded on 2026-09-22** by one extending to
79.800 so Colombo falls inside it; the mosaic gap below is a property of
the tile boundary, not of either box, and holds for both:

| | single scene | mosaic |
|---|---|---|
| tiles | T44NLN | T44NLN + T44NMN |
| AOI coverage | 0.9040 | **1.0000** |
| area described | 1164.06 km2 | **1289.16 km2** (+125.09) |
| observed surface | 848.22 km2 | **948.40 km2** (+100.18) |
| acquisition span | -- | 3.4 s, one datatake |

**The projected lattice is not the geographic one, and that is the whole
difficulty.** GLO-30 and GSW tiles sit on whole degrees, so a global lattice
suffices and `plan_mosaic` snaps to it. MGRS has no global lattice: every UTM
zone carries its own false easting, so two tiles are a whole number of pixels
apart *inside* a zone and an irrational number apart *across* one. T44NLN
begins at easting 399,960 and T44NMN at 499,980 -- 100,020 m, exactly 5001
pixels at 20 m. A projected mosaic is therefore anchored to the first tile's
own origin, every other tile must sit an integer number of pixels from it, and
a tile in another zone is **refused**: aligning those is a reprojection, which
this repo deliberately does not do.

Both refusals are checked rather than assumed, because a misaligned paste
returns an array of exactly the right shape whose values come from slightly the
wrong place, and nothing downstream can see that.

Two further rules the readers enforce:

- **Scaling is per tile.** Two scenes of one mosaic can carry different
  processing baselines, and the BOA offset does not cancel out of a normalised
  difference -- applying one scene's offset to the other's pixels would bias
  half the AOI by an amount no range check would catch.
- **Coverage is measured on values, not footprints.** A scene clipped by the
  swath edge still spans its whole tile, padded with zeros. Counting that as
  coverage would report a solidly observed AOI over a strip of nothing, so real
  data beats a neighbour's fill along every one of the 9.8 km overlaps.

`fetch_terrain_data` and `fetch_permanent_water` mosaic the same way, on the
geographic branch.

## Indices and band pairing

Which NIR band an index uses is not arbitrary. `nir` is B08 (10 m, 842 nm);
`nir08` is B8A (20 m, 865 nm), the band designed as B08's 20 m equivalent.

| Index | Formula | Bands | Range |
|---|---|---|---|
| `ndvi` | (NIR - Red) / (NIR + Red) | `nir`, `red` | [-1, 1] |
| `ndbi` | (SWIR16 - NIR08) / (SWIR16 + NIR08) | `swir16`, `nir08` | [-1, 1] |
| `bui` | NDBI - NDVI | derived | **[-2, 2]** |
| `ndmi` | (NIR - SWIR16) / (NIR + SWIR16) | `nir`, `swir16` | [-1, 1] |
| `mndwi` | (Green - SWIR16) / (Green + SWIR16) | `green`, `swir16` | [-1, 1] |
| `nmdi` | (NIR08 - (SWIR16 - SWIR22)) / (NIR08 + (SWIR16 - SWIR22)) | `nir08`, `swir16`, `swir22` | [-1, 1] when defined* |

NDBI pairs with `nir08` so that both its bands are native 20 m and neither is
interpolated. `bui` spans [-2, 2] because it is a difference of two normalised
differences -- do not assume [-1, 1] bounds for it.

**NDMI and NDBI are near-redundant, by construction.** On the same band pair
they are exact algebraic negatives. Using B08 for NDMI and B8A for NDBI makes
them merely near-negatives, which the live run confirms: NDBI mean -0.1481
against NDMI mean +0.1116. They are not independent evidence and should not be
treated as two votes. **NMDI is the one to use for soil moisture**: it takes
the SWIR *difference* as a term, so it is genuinely independent (mean +0.5632
on the same scene). And none of them is soil moisture in the physical sense --
that is the modelled soil moisture from `fetch_weather_data`. That workflow
labels it ERA5-Land, but it actually receives ECMWF IFS (open issue).

### NMDI is masked where it is undefined

NMDI = (NIR08 - g) / (NIR08 + g), with g = SWIR16 - SWIR22. Unlike a plain band
pair this is bounded to [-1, 1] **only while g > 0**. Where SWIR22 exceeds
SWIR16 the denominator falls below the numerator and the ratio diverges: on the
Shillong scene, before this was guarded, NMDI ran to **-249**.

g > 0 is also the index's physical premise. Wang & Qu define NMDI for soil and
canopy, where the 1.6 um band is brighter than the 2.2 um band. Water, shadow
and snow break that, and they are not surfaces NMDI describes. So pixels with
g <= 0.01 reflectance are masked, which bounds the result by construction
rather than by a tuned cutoff, and NMDI's `valid_fraction` is correspondingly
lower than the other indices'. Masked on the live scene, NMDI lands in
[0.218, 0.953] and its mean moves by 0.0016 -- the guard removes pathological
pixels, not signal.

Related: surface reflectance is clamped to [0, 1] before any index is computed.
Values outside that are unphysical but do occur once the -0.1 BOA offset is
applied to dark targets, and a negative reflectance propagates into a
denominator as a sign flip.

### The clamp saturates `min` and `max` over dark water

A consequence worth knowing before reading those two statistics. A band clamped
to exactly 0 makes a normalised difference against it exactly +-1, so where a
band reads near zero the indices report the clamp boundary rather than a
surface measurement.

**The trigger is a near-zero reflectance, not water as such.** Water is only
the usual cause, because water absorbs near-infrared -- but how much depends on
what is in it:

| | Edinburgh (coastal) | Tirupati (inland, post-monsoon) |
|---|---|---|
| SCL water pixels | 500 | 581 |
| Darkest NIR over water | below 0 | **0.049** |
| Saturated pixels | 73 | **0** |

Comparable amounts of water, opposite outcomes. Deep clear seawater is close to
a black surface in the infrared, so it tips below zero and clamps. Turbid water
carrying suspended sediment reflects off the particles -- 4.9% here, roughly
fifty times further from the bound than it needs to be -- so nothing clamps.

Practically: expect saturation over **coastal water and deep clear lakes**, and
none over **rivers, tanks, reservoirs and floodwater**, which are almost always
sediment-laden. Flood cases therefore tend to be the safe ones.

Measured on Edinburgh (`[-3.30, 55.90, -3.10, 56.00]`, scene
`S2A_T30UVG_20260811T113338_L2A`): NDBI reached max 1.0, and NDMI, MNDWI and
NDVI reached min -1.0. The cause is 25-73 pixels per band going slightly
negative before the clamp -- **at most 0.02% of 339153 valid pixels**.

So the saturation is negligible in extent but total in effect on the extremes:

- **`min` and `max` are not descriptive over an AOI containing optically deep,
  clear water.** Use `p10` and `p90`, which are interior and unaffected -- on
  the same scene NDBI's p90 was 0.0265 against its max of 1.0.
- Means and medians are effectively untouched at this pixel count.
- An NDBI of exactly 1.0 means "NIR clamped to zero", not "extremely built-up".

**This is counted, not left to the reader.** `stats.coverage.saturated_pixels`
sits beside `min` and `max`, so the caveat travels with the number:

```
index  min       max       p10       p90      saturated
ndvi   -1.0      1.0       0.2295    0.8712   70  (0.0206%)
ndbi   -0.6598   1.0      -0.38      0.0265   68  (0.0200%)
ndmi   -1.0      0.6448   -0.0656    0.3595   73  (0.0215%)
nmdi   -1.0      0.9625    0.4793    0.68      4  (0.0012%)
```

Shillong (0.02% water) and Tirupati (turbid water) both report **0 for every
index**, so the field discriminates rather than always firing. Rule of thumb:
**`p10`/`p90` to describe a distribution, `min`/`max` only to detect
saturation.**

**TODO(arid-bui):** the direction for BUI is unsettled -- see
[arid_region.md](arid_region.md) before treating the built-up fraction as an
estimate rather than an upper bound.

For why the built-up classification uses BUI, see
[urban_region_compatibility.md](urban_region_compatibility.md), and
[arid_region.md](arid_region.md) for where it breaks.

## Grid

Everything is computed on the scene's **native 20 m UTM grid**. Sentinel-2's
10 m and 20 m bands share one CRS and one origin, and 10980 is exactly 2 x
5490, so a 10 m band is read with a 2:1 averaging decimation onto the 20 m
grid. That is an exact average, not an interpolation, and it is why this
workflow needs no reprojection stack -- `pystac-client`, `rasterio` and `numpy`
only.

**Reflectance scaling is read from the STAC item, never hardcoded.** Baseline
05.00+ products carry a BOA offset of -0.1 alongside the 0.0001 scale, and that
offset does *not* cancel out of a normalised difference: the numerator cancels,
the denominator does not. Ignoring it biases every index.

## Storage

The raster does not go in the JSON. A 200 km² AOI at 20 m is half a million
pixels per index, and Monty's return value is read by a model. So:

- **On disk:** one COG per index, `int16` scaled by 10000, nodata `-32768`,
  deflate + predictor 2, 256 px tiles, with overviews. The ×10000 convention
  matches Sentinel-2's own reflectance encoding: half the size of float32, exact
  to 4 decimal places, and -32768 is unambiguous because valid indices are
  within [-2, 2]. Classifications are a separate `uint8` COG plus a legend.
- **In the JSON:** statistics, a 20-bin histogram, class fractions and areas,
  and the path.

## Response

```json
{"result_1": {
   "task": "fetch_lulc_data",
   "status": "success",
   "output": {
     "aoi": {"bbox": [91.8, 25.5, 91.95, 25.62], "area_km2": 203.7156},
     "scene": {"id": "S2C_T46RCP_20250510T043037_L2A",
               "datetime": "2025-05-10T04:41:...",
               "days_before_start": 85,
               "scene_cloud_cover": 17.135996,
               "aoi_valid_fraction": 0.6209,
               "processing_baseline": "05.11"},
     "grid": {"crs": "EPSG:32646", "resolution": 20.0,
              "resolution_units": "m", "pixel_area_km2": 0.0004,
              "shape": [671, 759], "transform": ["..."]},
     "indices": {
       "ndvi": {"range": [-1.0, 1.0],
                "stats": {"coverage": {"valid_fraction": 0.6209,
                                       "valid_pixels": 316209,
                                       "total_pixels": 509289,
                                       "saturated_pixels": 0,
                                       "saturated_fraction": 0.0},
                          "min": -0.1848, "max": 0.8692,
                          "mean": 0.5693, "median": 0.613,
                          "p10": 0.305, "p90": 0.7515,
                          "histogram": {"bin_edges": ["..."], "counts": ["..."]}},
                "classes": {"legend": {"5": "dense_vegetation", "...": ""},
                            "fractions": {"dense_vegetation": 0.5347, "...": 0},
                            "area_km2": {"dense_vegetation": 68.1, "...": 0},
                            "caveat": "Greenness bins, not land cover. ..."},
                "raster": {"path": "outputs/404/ndvi.tif",
                           "dtype": "int16", "scale": 0.0001,
                           "nodata": -32768, "crs": "EPSG:32646"}},
       "bui": {"range": [-2.0, 2.0], "stats": {"mean": -0.7173, "...": 0}}
     },
     "scl_counts": {"vegetation": 262719, "cloud_high_prob": 82309, "...": 0},
     "selection": {"lookback_days": 90,
                   "window": ["2025-05-05", "2025-08-03"],
                   "min_valid_fraction": 0.5,
                   "scenes_found": 13, "scenes_probed": 12,
                   "attempts": [{"scene_id": "...", "datetime": "...",
                                 "scene_cloud_cover": 46.0,
                                 "aoi_valid_fraction": 0.108,
                                 "rejected": "AOI valid fraction 0.108 below minimum 0.5"}]},
     "source": {"catalogue": "https://earth-search.aws.element84.com/v1",
                "collection": "sentinel-2-c1-l2a"}
   }}}
```

## `coverage` is the block to read first

Everything that qualifies a statistic sits under `stats.coverage`, so a caveat
cannot be read without the number it qualifies:

| Field | Means |
|---|---|
| `valid_fraction` | share of AOI pixels that survived masking |
| `valid_pixels` / `total_pixels` | the counts behind it |
| `saturated_pixels` | pixels sitting exactly on the index's bound |
| `saturated_fraction` | the same as a share of valid pixels |

`saturated_pixels` is `null`, not `0`, where saturation was not measured --
"not measured" and "none found" are different claims.

### `valid_fraction`

Every statistic is computed over the pixels that survived cloud masking. In the
run above that is **62% of the AOI** -- so the NDVI mean of 0.5693 is the mean
of the 62% that was visible, not of the AOI. A mean over 12% of an AOI is not
an AOI mean, and nothing downstream can tell the difference unless it looks.

`valid_fraction` appears on every index and on the scene record.

**TODO(entro): feed `valid_fraction` into the uncertainty score.** It is the
single most informative uncertainty input this workflow produces, and nothing
consumes it yet.

## Vegetation classes are provisional

`ndvi.classes` bins by **greenness**, and the bins are named for that:
`non_vegetation` (< 0.19), `low_greenness` (0.19-0.40), `moderate_greenness`
(0.40-0.50), `high_greenness` (0.50-0.60), `very_high_greenness` (>= 0.60) --
Aryal et al. (2022) breaks since 2026-11-19 (docs/ndvi_greenness_bins.md).
Results stored before use the old names; the example above is one of them.

They are deliberately **not** named Forest / Shrub / Agriculture. NDVI cannot
separate those: a dense crop canopy and closed-canopy forest both sit near
0.8. A
threshold that produced those labels would be wrong in a way that looks
plausible, which is worse than being obviously wrong.

The method for that split is an open design decision. Do not relabel these bins
until it is made.

## Behaviour worth knowing

- **Failure is loud.** No usable scene raises, rather than returning statistics
  computed from cloud. Monty isolates it as a per-task error.
- **Costs are network-bound**, roughly one windowed SCL read per probed scene
  plus one read per band on the accepted scene. `max_probes` bounds the worst
  case. This is seconds-to-minutes, not the sub-second `fetch_weather_data`
  call -- and the repo has no wall-clock guard around a task yet.
- **The AOI area differs slightly between workflows** (203.7 km² here, 201.1 km²
  from the DEM) because each snaps the bbox outward to its own native grid.
- **`bbox` order is `[west, south, east, north]`**, matching STAC. This is *not*
  the `[lat, lon]` ordering `fetch_weather_data` uses; a swap here is the most
  likely `signature_mismatch` Argos will have to debug.
