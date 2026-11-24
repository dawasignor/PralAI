# WORKFLOWS

The catalogue of everything Monty can run. One short entry each; the detail
lives in the per-workflow doc linked from it.

Citadel's analysis flows are a different thing and are catalogued separately in
[CITADEL_FLOWS.md](CITADEL_FLOWS.md). A Citadel flow consumes these functions;
it is not one of them.

**This file is for developers. Agents read the vault.** The same catalogue
exists as `Hand/Workflows/Monty data workflows.md` in the knowledge vault,
which is what an agent retrieves when it asks what it can run. The two must
agree, and `tests/test_workflow_catalogue.py` is what enforces that -- a
workflow in one and not the other fails the suite.

The vault note deliberately carries **no signatures**. Parameter names live in
the registry alone, because a name copied into a note drifts the moment the
function changes, and the result is a signature error that looks like a model
mistake.

```bash
python run.py --workflows      # the same list, generated from the registry
```

`core/workflows/` holds these functions. Registering one puts it in Argos'
prompt automatically, so a newly registered function is immediately callable --
never hardcode a function name in a prompt.

> **Adding a workflow? [Update this file](#adding-a-workflow) as part of it.**
> An out-of-date catalogue is worse than none, because it is believed.

---

## Catalogue

| Workflow | Does | Source | Doc |
|---|---|---|---|
| `fetch_weather_data` | Weather and soil-moisture time series for a point | Open-Meteo Archive + ERA5-Land | [fetch_weather_data.md](fetch_weather_data.md) |
| `fetch_lulc_data` | Land-cover indices for a bounding box | Sentinel-2 L2A via Earth Search | [fetch_lulc_data.md](fetch_lulc_data.md) |
| `fetch_terrain_data` | Elevation and slope for a bounding box | Copernicus DEM GLO-30 | [fetch_terrain_data.md](fetch_terrain_data.md) |
| `get_pop_data` | Population inside a bounding box, with an edge-band range | WorldPop Global2 R2025A (constrained, ~100 m) | [get_pop_data.md](get_pop_data.md) |
| `fetch_permanent_water` | Permanent/seasonal water baseline for a bounding box | JRC Global Surface Water | [fetch_permanent_water.md](fetch_permanent_water.md) |
| `fetch_place_context` | Bounding box, region, built-environment counts and matching disaster record for a place name | OSM Nominatim + Overpass + GDACS | [fetch_place_context.md](fetch_place_context.md) |
| `fetch_weather_grid` | Gridded hourly weather over a bbox, with storm totals, antecedent index and sensor-gate inputs | Open-Meteo Archive, model named: ECMWF IFS 9 km (default) or ERA5 | [fetch_weather_grid.md](fetch_weather_grid.md) |
| `mcdm_decision_matrix` | Module 6: the decision matrix and the event's weights, before any ranking | MCDM template v3 (fetches nothing) | [rank_flood_products.md](rank_flood_products.md#module-6-and-module-7-2026-11-19) |
| `rank_flood_products` | Ranks the five NRT flood products for one event by TOPSIS | MCDM template v3 (fetches nothing) | [rank_flood_products.md](rank_flood_products.md) |
| `sensitivity_analysis` | How far one criterion weight can move before that ranking changes | MCDM template v3 (fetches nothing) | [sensitivity_analysis.md](sensitivity_analysis.md) |
| `data_availability` | Which of the five NRT flood products observed a bbox, day by day, over an event window; draws a product-by-day chart | EODC STAC (GFM); NASA CMR-STAC (OPERA, LANCE); Open-Meteo (overpass cloud) | [data_availability.md](data_availability.md) |
| `where_am_i` | What an AOI is, any country: an OSM map of the bbox with the box drawn and major places and water infrastructure labelled; the first- and second-order admin units it lies in (levels read per country) and its cities and towns; counts of mapped buildings, hospitals, schools, commercial buildings, food or grocery shops, marketplaces and substations; and seven water-related influence categories (dams, reservoirs, gauges, water works, river crossings, floodplain industry, flood protection) | OpenStreetMap tiles; Overpass API | [where_am_i.md](where_am_i.md) |
| `map_hazard_exposure` | The hazard-and-exposure map of Modules 9 and 10: the stored flood extent as a red dotted hatch, hospitals, drinking-water facilities and food outlets (amber counted clusters, at most 2 km across), and each counted inside the flood, outside it and on unobserved ground; hazard layers are a registry | the stored `get_flood_map` extent + OpenStreetMap (Overpass, tiles) | [map_hazard_exposure.md](map_hazard_exposure.md) |
| `accuracy_metrics` | Agreement of each NRT product with a reference flood map, on its native grid, in one table beside the TOPSIS rank; Entro interprets it | UNOSAT reference (HDX zip); product STAC catalogues or local files; JRC GSW; Copernicus DEM WBM | [accuracy_metrics.md](accuracy_metrics.md) |

`rank_flood_products` and `sensitivity_analysis` fetch nothing at all and
consume what the others produced; the rest fetch data. None requires an
account, a key or asset signing. Every source and citation is in
[SOURCES.md](SOURCES.md).

---

## 1. `fetch_weather_data`

Historical hourly and daily series for **one point**, from the Open-Meteo
Archive. Seven variables: cloud cover, hourly rain, daily rain, wind speed,
shortwave radiation (GHI), and two ERA5-Land soil-moisture layers (0-7 cm and
7-28 cm).

Soil moisture is here rather than in `fetch_lulc_data` because Sentinel-2
cannot measure it -- no thermal channel, no L-band radar. The cost is
resolution: ERA5-Land is a ~9 km grid, so it is one value for the AOI. The
benefit is that cloud does not affect it.

Worth knowing: coordinates snap to the model grid, so `output.location` echoes
the cell actually used. `timezone` defaults to GMT, which makes daily buckets
UTC days -- **an open decision**, not settled.

## 2. `fetch_lulc_data`

Six land-cover indices for a **bounding box**, on Sentinel-2's native 20 m UTM
grid: NDVI, NDBI, BUI, NDMI, NMDI, MNDWI.

The interesting part is scene selection. A flood is a cloudy-season event, so
the pre-event window is the worst window Sentinel-2 has -- over Shillong, a
15-day lookback gave five scenes and none under 70% cloud. So it walks back up
to `lookback_days` (default 90) and takes the **most recent usable** scene, not
the clearest, and decides usability by reading SCL over the AOI rather than
trusting the catalogue's tile-wide `eo:cloud_cover`.

Five things about that search are worth knowing before calling it:

- **`min_valid_fraction` defaults to 0.7.** It has been 0.7, 0.9, 0.8 and 0.7
  again; the 0.9 and 0.8 settings were weighed against valid fractions that the
  pre-2026-09-22 `plan_grid` clamp had inflated, and those figures are
  withdrawn. See docs/fetch_lulc_data.md. Most monsoon AOIs still fail at the
  defaults, and that failure is the result.
- **A scene must cover the whole AOI** (`min_aoi_coverage`, default 1.0). An
  AOI straddling two MGRS tiles is refused rather than silently truncated to the
  part inside one scene. `aoi.bbox_requested`, `aoi.bbox_covered` and
  `aoi.coverage` are on every response.
- **A refused AOI is then mosaicked** (`mosaic`, default true). Same-date scenes
  from the neighbouring tiles are pasted onto one grid -- a fallback, never a
  first choice, since one scene is one acquisition. Over the Kelani basin this
  takes coverage from 0.904 to 1.000, recovering 125.09 km2 of 1289.16.
  `scene.is_mosaic` and `scene.acquisition_span_seconds` say whether what you
  have is one observation (span 0, a single datatake) or several pasted
  together. Tiles in different UTM zones are refused, not reprojected.
- **Probes are decimated, results are not** (`probe_decimation`, default 8).
  Candidates are screened by reading SCL at 1/8 through the COG's overviews --
  5.1x faster, worst-case error 0.008 -- and any scene that clears the threshold
  is re-read at full resolution before it is accepted. `estimated: true` in
  `selection.attempts` marks a record that is an estimate; the accepted scene is
  never one.
- **The AOI window is computed once per MGRS tile**, not once per candidate.
  Scenes the catalogue does not identify, or from a different tile, compute
  their own.

A weather-based pre-screen was tested against 77 live scenes and rejected: ERA5
cloud does not predict AOI usability at any cutoff. Do not re-propose it without
new evidence.

**TODO(arid-bui):** BUI needs a better direction and is unvalidated over
bare ground; see [arid_region.md](arid_region.md).

Built-up classification uses BUI, not NDBI -- see
[urban_region_compatibility.md](urban_region_compatibility.md), and
[arid_region.md](arid_region.md) for where that breaks.

The NDVI classes are **greenness bins, not land cover**. See
[ndvi_greenness_bins.md](ndvi_greenness_bins.md).

## 3. `fetch_terrain_data`

Elevation and slope for a **bounding box**, by Horn's method on Copernicus DEM
GLO-30, with a five-class slope breakdown.

Separate from `fetch_lulc_data` on purpose: Sentinel-2 has no elevation
information at all, terrain does not change between events so results cache
indefinitely, and bundling it would let a cloudy season take slope down too.

Limits: one 1-degree tile, and GLO-30 is a *surface* model, so slope over dense
forest is partly canopy roughness.

## 4. `fetch_permanent_water`

Permanent, seasonal and ephemeral water extent for a **bounding box**, from JRC
Global Surface Water occurrence (1984-2021, 30 m).

Not derived from Sentinel-2, because that would be circular -- a baseline built
from the same scenes that observe the flood makes floodwater look permanent.
The intended pairing is `MNDWI now` minus `GSW occurrence >= 90` for flood
extent.

Limits: one 10-degree tile, and the archive ends in 2021.

## 5. `fetch_place_context`

A **place name** to a bounding box, what is built inside it, and the disaster
record that matches it. Every other workflow here takes a `bbox` as given;
this is the one that produces it.

Three lookups, in dependency order: geopy/Nominatim for the box and region
(required), Overpass for counts of mapped buildings, homes, apartments,
commercial premises, hospitals and schools (optional), then GDACS for the event
(optional).

That order is a bug fix. Tagging the AOI to a register entry used to happen on
the way to the bounding box, so a register that answered with nothing, or with
two equally good candidates, took the bounding box down with it -- a city's
extent does not depend on which disaster is being tagged to it.

Limits: Overpass is the slow step (~2 min measured over Derna, deliberately
paced against one public endpoint); AOIs over 5,000 km2 are refused for the feature count; counts are of
*mapped* features and a failed category is absent rather than `0`.

## 6. `fetch_weather_grid`

Hourly weather over a **bounding box**, sampled on a grid and aggregated across
it: `mean`, `max`, `min`, `p90` and `area_frac_*` per hour per variable, plus
storm totals, peak intensities, an antecedent precipitation index, rain bursts
and the inputs to the sensor gates.

Distinct from `fetch_weather_data`, which describes a **point**. The area
statistics are the reason: "mean wind 4 m/s" and "wind over 5 m/s across 70% of
the AOI" can be the same hour, and only the second says whether a SAR scene is
roughened.

Keeps the raw response and its SHA-256 as the receipt -- which is why it talks
to the JSON endpoint directly rather than through the flatbuffer SDK
`weather.py` uses.

**The model is named in every request** (since 2026-09-26): `ecmwf_ifs` by
default, `era5` on request, `best_match` refused. Before that the request named
none, Open-Meteo's `best_match` served ECMWF IFS, and the payload called it
ERA5 at 31 km -- including the Kelani outputs. See `fetch_weather_grid.md`.

Limits: a 9 km (IFS) or 31 km (ERA5) model gates products delivered at
20-250 m; sampling finer than the model grid is interpolated, so extra points
are distinct but not independent; sea points are not masked; dates are UTC and
cannot pass today in UTC; a missing hour is reported in `qc.gaps` and any figure
needing it is withheld, never filled; `acquisitions` is empty until a scene catalogue is
wired in, and is deliberately not filled with plausible overpass times.

---

## 7. `rank_flood_products`

Ranks the five NRT flood-mapping products for one event by TOPSIS. Full
reference: `docs/rank_flood_products.md`.

**The only workflow that fetches nothing.** It consumes the weather, LULC and
terrain payloads the others produced, so it is the last call in a chain rather
than a starting point, and it takes five numbers rather than a bounding box:
AOI cloud cover, wind speed, dense-vegetation share, steep share, built-up
share.

Four things to know before reading a ranking from it:

- **The event changes the WEIGHTS, not the product scores.** A product is no
  better at seeing through cloud on a clear day; what changes is how much its
  cloud-blindness matters.
- **High cloud no longer rejects an optical product** (changed 2026-09-22).
  `cloud_gate` moved from 80 to 100, so it cannot fire. The gate fired on a
  window-MEAN, and feasibility is per-overpass: at 91.67% mean cloud over the
  Kelani event, 2 of 16 daytime overpasses were still under 80%. Rejection
  asserted zero. **The cost is that a rank is no longer a usability claim** --
  check `W1` and the cloud index first. See the reference doc for the full
  argument and its tension with the vault's availability ruling.
- **Only TOPSIS exists.** The specification's weighted-sum robustness check and
  the Spearman comparison are not implemented, so no ranking from this
  workflow is method-agnostic.
- **Thirty evidence cells carry basis, rationale and sources**, five of them
  marked contested. The matrix is not a table of bare numbers.

Verified against the specification workbook's six test cases to 4.14e-13.

## 8. `sensitivity_analysis`

How far one criterion weight can move before the ranking from
`rank_flood_products` stops holding. Full reference:
`docs/sensitivity_analysis.md`.

**The second workflow that fetches nothing**, and the only one that consumes
no payload either: it takes the same five event indices and re-runs the
ranking itself, so the baseline it measures from cannot be a different event's
ranking. That baseline is checked against the ranking it produced before any
sweep runs.

Four things to know before quoting a threshold from it:

- **Weights only.** The product scores, the contested evidence cells, the
  unresolved P2 range and the author parameters are all held fixed. Stability
  here is not stability against a different score.
- **It starts from the weights Module 7 ranked with** (2026-11-22): the
  event-first weights and any weight or score the user changed. A sweep of a
  normalised weight is exactly what stating that one weight to the ranking
  does, so each threshold is reported with the weight to state
  (`equivalent_stated_weight`); nothing is unreachable.
- **One weight at a time.** Two criteria moving together can cross a threshold
  neither crosses alone. That is output 6's question, not this one's.
- **The fractions are shares of the weight range, not probabilities.** Nothing
  here says a weight is likely.

Verified against a closed form: a two-by-two problem whose crossing is exactly
0.5, which a grid cannot land on and the bisection must find.

## 9. `data_availability`

Which of the five NRT flood products observed the AOI on each day of the event
window -- the glance before the suitability assessment. Full reference:
`docs/data_availability.md`.

Three things to know before reading its chart:

- **An instance is one acquisition, not one file.** Files within 10 minutes of
  each other are one pass, however many tiles or reprocessed versions.
- **MODIS MCDWD and VIIRS VCDWD keep about a week of archive.** Older days are
  `not_checkable`, never `none`; both are produced daily, so those cells carry
  the model cloud cover at the sensor's overpass instead.
- **Days are local to the AOI**, from the civil UTC offset. An instance says a
  file touches the AOI, not how much of it.

## 10. `accuracy_metrics`

Agreement of each NRT product with a reference flood map, from one
`config.yaml`. Full reference: `docs/accuracy_metrics.md`.

**The only workflow whose result goes straight to another agent.** Entro's
`accuracy_interpretation` card declares `after_workflow: accuracy_metrics`, so
when it succeeds Argus routes the table to Entro before answering, in the same
run. Monty owns the numbers; Entro owns what they mean.

- **Agreement, not accuracy** -- the reference is an operational map.
- **Native grid** -- the reference is aggregated to each product's pixels;
  no-data (including GFM's Exclusion Mask) lowers coverage, never adds
  non-flood.
- **Nothing is guessed** -- encodings, paths, window and thresholds come from
  the config; a `TODO` stops the run. A product that cannot be compared keeps
  its row with the reason.

## Conventions shared across workflows

Read this before adding one; consistency here is what keeps Argos' job simple.

**Argument shape.** `params` bind straight to the function signature, so
parameter names are a public API. Renaming one is a breaking change.

**Geometry, and the trap in it.** Point workflows take `latitude, longitude`;
area workflows take `bbox` as `[west, south, east, north]` (STAC order). Those
are opposite conventions for the same planet, and a swap is the single most
likely `signature_mismatch` Argos has to debug.

| Workflow | Geometry |
|---|---|
| `fetch_weather_data` | `latitude`, `longitude` |
| all others | `bbox` = `[west, south, east, north]` |

**Times are local to the AOI.** Dates in and dates out mean local time at the
coordinates, never UTC. `fetch_weather_data` resolves the zone from the
coordinates by default (`timezone="auto"`) and returns timestamps carrying that
offset. Daily aggregation buckets on the local midnight, so this changes the
numbers, not just the labels -- over Wayanad the same day returned 26.6 mm
local against 23.3 mm UTC. Any future workflow that aggregates by day must
follow the same rule. Details and the measured comparison are in
[fetch_weather_data.md](fetch_weather_data.md#disclaimer-all-times-are-local-to-the-aoi).

> **TODO: Take care of timezone while implementing UI.** A date picked in the
> UI is in the user's zone, which is not necessarily the AOI's. Be explicit
> about which, and show the resolved `location.timezone` next to any daily
> figure.

**Raster outputs.** Anything producing a raster takes `output_dir` (default
`outputs`), `event_id` (the subdirectory), and `write_rasters` (set false
to compute statistics without writing). Rasters go to disk and **statistics go
into the JSON** -- Monty's return value is read by a model, and an AOI at 20 m
is half a million pixels per index.

**Caveats travel with the numbers they qualify.** Every raster statistic sits
next to a `coverage` block rather than relying on prose someone has to
remember:

- `valid_fraction` -- the share of pixels that survived masking. Every mean is
  a mean over that subset (62% of the AOI on the live Shillong run), and
  nothing downstream can tell a 62% mean from a 12% one unless it is told.
- `saturated_pixels` -- pixels sitting exactly on a bound. Reflectance is
  clamped to [0, 1], so a band reading near zero becomes exactly zero and a
  normalised difference against zero is exactly +-1. The trigger is darkness,
  not water as such: Edinburgh's clear coastal water saturated 73 of 339153
  pixels, while Tirupati's turbid post-monsoon water -- 4.9% NIR, similar pixel
  count -- saturated none.

So: **`p10`/`p90` describe a distribution, `min`/`max` only detect
saturation.** Both are returned -- deleting `min`/`max` would remove the
detector along with the trap.

Counting saturation is opt-in per quantity, because a bound is not always an
artefact: slope really is 0 degrees on flat ground and JRC occurrence really
does reach 100. It is `null` where not measured, never `0`.
*TODO(entro): nothing consumes `coverage` yet.*

**Failure is loud.** Raise on failure; per-task isolation is handled for you,
and one failing task never stops the batch. Never return a plausible number
computed from bad input -- the Sentinel-2 workflow fails outright rather than
reporting statistics derived from cloud.

**Provenance comes back with the answer.** Echo what was actually used: the
resolved grid cell, the selected scene and the scenes rejected, the tile URL.
A number in a thesis needs to be traceable to the pixel it came from.

---

## Adding a workflow

1. Write the function in `core/workflows/<topic>.py`, keyword arguments only.
2. Decorate it: `@workflow("name", "one-line description")`.
3. Import the module in `core/workflows/__init__.py`.
4. Return JSON-serialisable data; raise on failure.
5. Add its sources to [SOURCES.md](SOURCES.md) **before** it computes anything.
6. Write `docs/<name>.md`.
7. **Add it to the catalogue table and a section above.**
8. `python run.py --workflows` to confirm it registers.



---

## Planned

Specified but not built. Listed so they are not re-derived from scratch.

| Workflow | Would do | Spec |
|---|---|---|
| `fetch_vegetation_phenology` | Separate forest / shrub / cropland by seasonal NDVI amplitude across a year | [ndvi_greenness_bins.md](ndvi_greenness_bins.md) |

It needs a **scene stack** rather than the single scene `fetch_lulc_data`
selects, which is why it is a separate workflow rather than more parameters on
the existing one.

Other ideas raised and not committed to: river levels, building footprints for
exposure ([urban_region_compatibility.md](urban_region_compatibility.md)), HAND
or TWI from the DEM already fetched, and Sentinel-1 SAR for flood extent under
cloud.
