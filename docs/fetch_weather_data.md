# Workflow: `fetch_weather_data`

Historical weather time series for one point and date range, from the
[Open-Meteo Archive API](https://open-meteo.com/en/docs/historical-weather-api).

Implemented in [`core/workflows/weather.py`](../core/workflows/weather.py).
Registered as `fetch_weather_data`, so Argos can call it by name.

## Request

```json
{"event_id": 42,
 "tasks": [
   {"function": "fetch_weather_data",
    "params": {"latitude": 55.9533,
               "longitude": -3.1883,
               "start_date": "2026-08-18",
               "end_date": "2026-08-20"}}
 ]}
```

| Param | Type | Required | Notes |
|---|---|---|---|
| `latitude` | float | yes | Decimal degrees. |
| `longitude` | float | yes | Decimal degrees. |
| `start_date` | str | yes | `YYYY-MM-DD`, inclusive. |
| `end_date` | str | yes | `YYYY-MM-DD`, inclusive. |
| `variables` | list[str] | no | Subset of the five below. Omit for all. |
| `timezone` | str | no | IANA name, or `auto` (default) to resolve it from the coordinates. See the disclaimer below. |

## Disclaimer: all times are local to the AOI

**Dates in and dates out mean local time at the coordinates, never UTC.**
`timezone` defaults to `auto`, which asks Open-Meteo to resolve the zone from
the latitude and longitude, so a date means the same thing to this workflow as
it does to somebody standing in the catchment.

This is not cosmetic. **Daily aggregation buckets on the local midnight, so the
zone changes the numbers.** Measured over Wayanad, Kerala for 29 July 2026:

| Timezone | daily_rain (mm) | 3-day total |
|---|---|---|
| `Asia/Kolkata` (what `auto` resolves) | **26.6**, 22.8, 29.4 | 78.8 |
| `GMT` | 23.3, 23.5, 29.1 | 75.9 |
| `Europe/Berlin` | 24.7, 22.8, 29.9 | 77.4 |

Day one moves by 3.3 mm — about 13% — purely from where the bucket boundary
falls. For a rainfall threshold, that is the difference between triggering and
not.

Two consequences to keep straight:

- **Timestamps carry the local offset**, e.g. `2026-07-29T00:00:00+05:30`.
  They are *not* rendered in UTC. That matters because the same instant printed
  as UTC reads `2026-07-28T18:30:00+00:00` — the day *before* the one it
  describes. Slicing the first ten characters off a UTC-rendered local day
  silently reports the wrong date.
- **`location.timezone` is the resolved zone, not what you asked for.**
  `timezone_requested` echoes the request separately. Check the resolved one;
  do not assume an override was sensible.

Passing an explicit zone that is not the AOI's is almost always a mistake. The
parameter exists for the case where a study deliberately aggregates on some
other calendar, and that intent should be recorded wherever the result is used.

> **TODO: Take care of timezone while implementing UI.** A user picking "29
> July" in a date picker means their own local 29 July, which is not
> necessarily the AOI's. The UI must be explicit about which zone a date is in
> and display the resolved `location.timezone` alongside any daily figure.

## Variables

One HTTP call fetches everything; one function extracts each series.

| Output key | Open-Meteo variable | Resolution | Unit | Function |
|---|---|---|---|---|
| `cloud_cover` | `cloud_cover` | hourly | % | `cloud_cover()` |
| `hourly_rain` | `rain` | hourly | mm | `hourly_rain()` |
| `daily_rain` | `rain_sum` | daily | mm | `daily_rain()` |
| `wind_speed` | `wind_speed_10m` | hourly | km/h | `wind_speed()` |
| `shortwave_radiation` | `shortwave_radiation` (GHI) | hourly | W/m² | `shortwave_radiation()` |
| `soil_moisture_surface` | `soil_moisture_0_to_7cm` | hourly | m³/m³ | `soil_moisture_surface()` |
| `soil_moisture_root_zone` | `soil_moisture_7_to_28cm` | hourly | m³/m³ | `soil_moisture_root_zone()` |

## Soil moisture

The last two come from **ERA5-Land reanalysis** and are here because
**Sentinel-2 cannot measure soil moisture** — it has neither a thermal channel
nor an L-band radar, the two instruments the quantity is actually retrieved
from. See [SOURCES.md](SOURCES.md) for the reference.

This is genuine volumetric soil water in m³/m³, not an optical proxy. The trade
is resolution: ERA5-Land is a ~9 km grid, so it is **one value for the whole
AOI**, with no spatial pattern. Against that, it is unaffected by cloud — which
matters, because the pre-event window is precisely when Sentinel-2 is least
usable (see [fetch_lulc_data.md](fetch_lulc_data.md)).

The spatial companion is **NMDI** from `fetch_lulc_data`. Treat these two as
answering different questions: ERA5-Land gives the magnitude, NMDI gives the
pattern. NDMI is *not* a third opinion — it is very nearly the algebraic
negative of NDBI.

## Response

Each variable is its own list, paired with its own timestamps:

```json
{"result_1": {
   "task": "fetch_weather_data",
   "status": "success",
   "message": "fetch_weather_data completed successfully",
   "error": null,
   "output": {
     "location": {"latitude": 55.9227, "longitude": -3.1288,
                  "elevation_m": 69.0,
                  "timezone": "Europe/London",
                  "timezone_abbreviation": "GMT+1",
                  "timezone_requested": "auto",
                  "utc_offset_seconds": 3600},
     "period": {"start_date": "2026-08-18", "end_date": "2026-08-20"},
     "series": {
       "cloud_cover":         {"time": ["2026-08-18T00:00:00+01:00", "..."],
                               "values": [93, 88, "..."], "unit": "%"},
       "hourly_rain":         {"time": ["..."], "values": [0.0, 0.1], "unit": "mm"},
       "daily_rain":          {"time": ["..."], "values": [0.2, 7.5, 2.0], "unit": "mm"},
       "wind_speed":          {"time": ["..."], "values": [11.2], "unit": "km/h"},
       "shortwave_radiation": {"time": ["..."], "values": [0.0, 505.0], "unit": "W/m^2"}
     },
     "counts": {"cloud_cover": 72, "daily_rain": 3, "...": 0},
     "source": "https://archive-api.open-meteo.com/v1/archive"
   }}}
```

Timestamps are ISO-8601 **in the AOI's local time**, carrying the resolved
offset (`2026-07-29T00:00:00+05:30`). Hourly series have `24 x days` points; daily
series have one per day.

## Behaviour worth knowing

- **Coordinates are snapped.** `location` echoes what the API resolved, not
  what you asked for — Open-Meteo returns the nearest grid cell. Asking for
  Edinburgh (55.9533, -3.1883) returns 55.9227, -3.1288, ~7 km away. Use the
  echoed values when reporting provenance.
- **Gaps are `null`, never a number.** Open-Meteo uses NaN for missing
  observations and JSON has no NaN, so gaps become `null` rather than a value
  that silently lies.
- **Archive lag.** The archive trails real time by roughly five days. A range
  ending too recently returns fewer points than requested, or nulls.
- **Responses are cached for an hour** in `.cache` (`requests_cache`), with
  five retries and backoff. Archive data is immutable, so a repeated run over
  the same window costs no request.
- **Request order is load-bearing.** The SDK returns variables by index in the
  order they were requested. `_HOURLY` and `_DAILY` in `weather.py` pin that
  order; reordering one without the other silently mislabels every series.

## Failure modes

| `error_type` | Cause | Fix |
|---|---|---|
| `signature_mismatch` | `lat`/`lon` instead of `latitude`/`longitude` | Use the exact parameter names. |
| `runtime_error` | Unknown entry in `variables`, malformed date, network failure | Read the message; it lists the valid variable names. |
| `unknown_function` | Name misspelled in `tasks` | `python run.py --workflows`. |

Errors are returned, never raised — one failing task does not stop the batch.

## Run it

```bash
python run.py --monty '{"event_id": 42, "tasks": [{"function": "fetch_weather_data", "params": {"latitude": 55.9533, "longitude": -3.1883, "start_date": "2026-08-18", "end_date": "2026-08-20"}}]}'
```

Add `--full` to print whole series instead of a head. No API key is needed —
Open-Meteo's non-commercial tier is unauthenticated.

## Dependencies

```
openmeteo-requests    requests-cache    retry-requests    pandas
```

Imported lazily, so the rest of the repo runs without them installed.

## Adding another variable

1. Add the Open-Meteo name to `_HOURLY` or `_DAILY` in `weather.py`
   (**append** — do not reorder).
2. Add its unit to `_UNITS`.
3. Write a one-line extractor next to the others and register it in
   `_EXTRACTORS`.
4. Update the table above.
