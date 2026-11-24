# Workflow: `fetch_weather_grid`

Hourly weather over a **bounding box** for an event window, sampled on a grid
and aggregated across it, with the storm summary and the sensor-gate inputs.

Implemented in [`core/workflows/weather_grid.py`](../core/workflows/weather_grid.py).
Output contract: `Hand/Workflows/Weather output schema.md` in the vault.
Data source and citations: [SOURCES.md](SOURCES.md).

## Why this exists alongside `fetch_weather_data`

They answer different questions. `fetch_weather_data` describes a **point**;
this describes an **area**, and the MCDM consumes the area.

The difference that matters is `area_frac_*`. "Mean wind 4 m/s" and "wind over
5 m/s across 70% of the AOI" can be the same hour, and only the second tells
you whether a SAR scene is roughened. A mean over an AOI hides exactly the
spatial concentration that decides whether a product works.

It also keeps the raw response and its SHA-256 as a receipt, which is why it
calls the JSON endpoint directly instead of using the flatbuffer SDK that
`weather.py` uses -- a flatbuffer response cannot be hashed into a receipt.

## Request

```json
{"event_id": "kelani_2025",
 "tasks": [
   {"function": "fetch_weather_grid",
    "params": {"bbox": [79.8, 6.783, 80.217, 7.083],
               "start_date": "2025-11-24", "end_date": "2025-12-01",
               "event_id": "kelani_2025", "model": "ecmwf_ifs",
               "spacing_deg": 0.1, "antecedent_days": 30,
               "local_utc_offset_h": 5.5}}
 ]}
```

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `bbox` | list[float] | yes | | `[west, south, east, north]`, WGS84. |
| `start_date` / `end_date` | str | yes | | ISO dates, both inclusive, **in UTC**. The archive refuses an `end_date` later than today in UTC -- which is tomorrow's date east of Greenwich for part of every day. |
| `event_id` | str | no | `""` | Names the event in `meta`. |
| `model` | str | no | `ecmwf_ifs` | `ecmwf_ifs` or `era5`. Named in every request and written into `meta.source`. `best_match` is refused -- see below. |
| `spacing_deg` | float | no | the model's | 0.1 for `ecmwf_ifs`, 0.25 for `era5`: the round spacing not finer than the model grid. |
| `antecedent_days` | int | no | `30` | Extra days fetched *before* the window, for the antecedent index. |
| `local_utc_offset_h` | float | no | the event's zone | For rendering local times; storage is always UTC. Left out (since 2026-11-22): the zone Module 1 stored for the session, else the same Open-Meteo zone lookup, else solar -- recorded in `meta.local_timezone` and `meta.local_offset_source`. It used to default to 0. |
| `aoi_mask` | str | no | `""` | Recorded, **not applied**; raises a qc warning. |
| `burst_gap_hours` | int | no | `6` | Dry hours that separate one rain burst from the next. |
| `burst_min_mm` | float | no | `10.0` | Bursts below this depth are dropped. |
| `output_dir` | str | no | `outputs` | Where the hyetograph goes: `<output_dir>/weather/<event_id>/hyetograph.png`. |
| `write_figure` | bool | no | `true` | `false` returns the payload without drawing the hyetograph. |

## The model is named, always (since 2026-09-26)

Until 2026-09-26 the request carried no `models` parameter, so Open-Meteo used
its default, `best_match`, which "combines IFS HRES, ERA5 and ERA5-Land
seamlessly" -- the server chooses per request. The payload was labelled
"best_match (ERA5 for atmosphere, ERA5-Land for soil)", 31 km, by hand.

**It was ECMWF IFS, 9 km.** Re-fetching the cached Kelani grid with the model
named: `ecmwf_ifs` reproduces it in 192 of 192 hours for precipitation, cloud
and wind, and the storm total exactly (390.97 mm); `era5` matches 43, 94 and 0
of 192 and gives 375.60 mm. Over Bangkok, best_match equalled `ecmwf_ifs` in
264 of 264 hours, including days ERA5 had already published. The numbers were
never wrong; the model named for them was.

Now:

| `model` | Resolution | Grid | Default spacing | Record | Delay |
|---|---|---|---|---|---|
| `ecmwf_ifs` (default) | 9 km | ~0.081 deg | 0.1 deg | 2017-01-01 onwards | none; updated every 6 h |
| `era5` | 31 km (served on 0.25 deg) | 0.25 deg | 0.25 deg | 1940 onwards | about 5 days |

- `meta.source.model`, `model_id`, `native_res_km` and `model_delay` are
  written from the request, so the label cannot drift from what was served.
- **`best_match` is refused**, as is any model not in the table: its identity
  is decided server-side, which is exactly what made the old label wrong.
- **`ecmwf_ifs` refuses a fetch starting before 2017-01-01** -- the antecedent
  days count -- because the archive answers that with nulls, not an error.
  Pass `model="era5"` for an earlier event (Chennai 2015, for example).
- **Hours at or after the retrieval time are flagged as forecast** in qc. A
  window that reaches "now" ends in hours the model has not analysed yet.
- **`era5` over a recent window returns nulls, not an error**, for its last
  ~5-6 days (measured: 144 of 144 hours null for 20-25 Sep 2026). Those hours
  are gaps, and are reported and withheld as below -- never summed as dry.

Why IFS is the default: it is what every cached weather output has actually
been, so the Kelani outputs stay reproducible under their true name,
and it is the only archive model that reaches a flood happening now.
`tests/test_weather_grid.py` holds all of the above; `--live` re-fetches Kelani
and checks it reproduces exactly.

## Gaps are reported, never filled (since 2026-09-26)

The user's rule: **a missing hour is not assumed or filled in -- it is
reported.** Before this, the storm series was built with `_mean(row) or 0.0`,
so a missing hour entered every total as a dry one. Live ERA5 over Bangkok for
18-25 Sep has 120 of 192 hours missing; the old code would have called the 50.78
mm that fell in the other 72 hours the storm total.

- **An hour counts only if every grid point returned a value.** A partial hour
  is a gap too: a mean over the points that reported is a guess about the rest.
- **Hourly aggregates are `None` at a gap**, in every statistic.
- **A figure that needs a missing hour is withheld** (`None`) and its path is
  listed in `qc.gaps.withheld`. That covers storm totals, peak intensities, an
  antecedent index whose tail crosses an incomplete day, the pressure and gust
  extremes, dry hours after the last burst, ET0 after the peak, and the
  precipitation CV.
- **A bound, where one exists, is given beside it and labelled.** Rain that fell
  in the observed hours cannot un-fall, so `storm_total_mm` carries
  `observed_hours_lower_bound_mm`, and `max_intensity_mm` carries
  `observed_windows_lower_bound`. They are floors, never the answer.
- **A rain burst touching a gap is kept and flagged**
  (`touches_gap`, `depth_is_lower_bound`), even under the depth threshold --
  "too small to report" cannot be known.
- **`qc.gaps` is on every payload.** It holds the policy, `window_complete`,
  `antecedent_complete`, and per-variable missing hours as UTC ranges, split
  between event and antecedent. It also lists what was not served, and what was
  withheld. An empty `by_var` is the claim "nothing was missing", made out loud.

The output document opens with **"Uncertainty: data completeness"**, before any
number. It says *Complete*, or tabulates the gaps and lists what they withhold.
Withheld figures read *withheld (gaps)*, and the daily rollup has a `missing h`
column. A payload fetched before the audit is rendered from
`qc.missing_frac_by_var`; if that shows gaps, the document warns that the
payload's totals were computed with gaps as 0 mm.

On complete data nothing changes: the gap-aware summary reproduces the cached
Kelani summary exactly (`tests/test_weather_grid.py --live`).

## A refusal says why

The archive answers a bad request with HTTP 400 and a JSON `reason`. `_get`
raises `OpenMeteoError` with that reason in the message, e.g. "Open-Meteo
refused the request (HTTP 400): Parameter 'end_date' is out of allowed range
from 1940-01-01 to 2026-09-25". Before 2026-09-26 only urllib's "HTTP Error
400: Bad Request" reached the task error, which gave Argos' debugger nothing to
patch.

## Two things that will bite

**Wind must be requested in m/s.** Open-Meteo answers in km/h by default. The
query pins `wind_speed_unit=ms`; without it every wind figure is 3.6x too large
under an `m/s` label, and the `> 5 m/s` roughening gate fires on 1.4 m/s air.
The numbers stay plausible and the unit string stays correct, so nothing
downstream catches it. This was a live bug on the first Kelani run.

**Wind direction is circular.** It is combined as a unit-vector resultant.
The arithmetic mean of 350 deg and 10 deg is 180 -- due south for two readings
that are both nearly due north.

## Sampling finer than the model grid

`spacing_deg` defaults to the model's own spacing. Going finer is allowed -- a
small AOI yields few points at native spacing, a thin basis for an area
fraction -- but the extra points are **not extra information**: the endpoint
interpolates between model nodes, so neighbouring points are correlated by
construction. When `spacing_deg` is finer than the model grid a qc warning
says so: `precipitation_cv` then **understates** true sub-grid heterogeneity,
and an area fraction counts interpolated points rather than model cells.

**Correction (2026-09-26).** This section used to cite a measurement -- four
points across 0.3 deg came back as four distinct series -- as evidence of
interpolation on a 0.25 deg ERA5 grid. That data was IFS at 9 km, so the four
points were most likely four different model cells. The warning stands on the
general point; the measurement does not support it, and the Kelani runs at 0.1
deg were not oversampled at all.

## `acquisitions` comes back empty

The block pairs each candidate product's overpass with the weather at that
instant and computes the per-scene gates (`sar_wind_roughening`,
`optical_usable`). Open-Meteo does not know when Sentinel-1 flew, so the
acquisition times are not available here.

It is left **empty rather than plausible**. A gate is a claim about a specific
scene, and an invented overpass time makes that claim unfalsifiable. A scene
catalogue has to be wired in before this output can feed the MCDM; until then
the qc warning says so on every run.

`cloud_cover_scene_pct` stays null for a different reason: it comes from the
scene's own mask (Fmask, MOD35), not from a weather model. Do not substitute
the model cloud for it -- a 9 km (IFS) or 31 km (ERA5) cell mean and a
per-pixel scene mask are not the same measurement.

## The hyetograph and the summary (since 2026-10-14)

Output 1 reports rainfall as a **hyetograph**, not a table (user decision,
2026-10-14). After a fetch the workflow draws `core.charts.hyetograph` -- the
hourly AOI-mean rainfall as bars, the rain accumulated since the window opened
as a line, rain bursts shaded with their depth, the wettest 24 h bracketed --
and records it under `figure`, which `core/figure_refs.register_run` turns into
`<image - run-region-year-hyetograph>` for Argus. A missing hour is shaded and
the cumulative line stops there; it is never counted as dry. A chart that
cannot be drawn adds a qc warning and costs the answer its image, never the
fetch.

`reports.weather_markdown` -- the template of `01_weather_data.md` -- shows the
hyetograph in a `## Rainfall hyetograph` section, and its daily rollup keeps
only cloud and wind.

**Argus is given the summary, not the document.** `core/weather_summary.py`
cuts the payload to the values output 1 is written from: storm totals, peaks,
antecedent index, bursts, daily or wettest-day rain, completeness, provenance,
and the window means of cloud and wind. Since 2026-11-22 those window means
are weather facts only: the ranking's `cloud_pct` and `wind_ms` are read at
the satellite passes in local time (`core/pass_weather.py`,
docs/rank_flood_products.md). On Kelani it is about 1.6 KB against 12 KB for the
document, and reproduces 390.97 mm, 91.67 % and 4.13 m/s.

## Other stated limits

- **A 9 km (IFS) or 31 km (ERA5) model, 20-250 m products.** Model wind and
  cloud gate products one to two orders of magnitude finer. Standing qc
  warning, naming the model.
- **No land mask.** Unlike the EO workflows, sea points are kept and equally
  weighted. Over Bangkok's Nominatim box 21 of 56 points are in the Gulf of
  Thailand.
- **No `aoi_mask` means bbox statistics, not basin statistics.** Points are
  equally weighted; naming a mask records it and does not apply it.
- **Over 200 grid points is refused** -- Open-Meteo will not serve the call.

## See also

- [WORKFLOWS.md](WORKFLOWS.md) -- the full catalogue.
- [fetch_weather_data.md](fetch_weather_data.md) -- the point version.
