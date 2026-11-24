"""Gridded event weather for an AOI, to the thesis weather-output schema.

`fetch_weather` is **the one weather tool** (since
2026-10-25; registered as `fetch_weather_grid` before that, and the resolver
still maps the old name). It answers: **what was the weather over this area,
hour by hour, and was it the kind of weather that breaks a flood-mapping
sensor.** The MCDM consumes it. The point tool `fetch_weather_data`
(`weather.py`) is retired from the registry; its file is kept.

Three things it does that the point tool could not:

  * **Samples the bbox at model resolution** and aggregates across the points,
    so a statistic is an area statistic. `area_frac_gt_*` is the one that
    matters for a gate: "mean wind 4 m/s" and "wind over 5 m/s across 70% of
    the AOI" are the same hour described two ways, and only the second tells
    you whether the SAR scene is roughened.
  * **Keeps the raw payload and its SHA-256.** A thesis number needs a
    receipt, and a flatbuffer response cannot be hashed into one -- which is
    why this talks to the JSON endpoint directly rather than through the SDK
    that `weather.py` uses.
  * **Separates measured from derived.** Everything Open-Meteo did not serve
    is listed in `derived` with the method that produced it.

Wind direction is a circular quantity and is combined as a **unit-vector
mean**. The arithmetic mean of 350 deg and 10 deg is 180 -- due south for two
readings that are both nearly due north. Nothing else in this module averages
an angle.

**`acquisitions` is returned empty.** It wants satellite overpass times for the
candidate products, and Open-Meteo has no idea when Sentinel-1 flew. Filling it
would mean inventing the acquisition times the gates are computed at, so it
stays empty with a qc warning until a scene-catalogue source is wired in.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import tool
from .. import observability

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TIMEOUT = 120

# The archive models this workflow will ask for, and what each one is.
#
# The model is ALWAYS named in the request. Open-Meteo's default, `best_match`,
# "combines IFS HRES, ERA5 and ERA5-Land seamlessly" -- the server decides
# which one answers. Until 2026-09-26 this module sent no `models` and labelled
# every payload "ERA5, 31 km"; over Kelani and Bangkok best_match was ECMWF IFS
# in 192 of 192 hours. A label written from what was requested cannot drift
# from what was served, so `best_match` is refused rather than accepted.
#
# `grid_deg` is what the model resolves, and decides the oversampling warning.
# `default_spacing_deg` is the round spacing that does not go finer than it.
# Figures from the Open-Meteo Historical Weather API data-source table.
MODELS: dict[str, dict[str, Any]] = {
    "ecmwf_ifs": {
        "label": "ECMWF IFS 9 km, assembled by Open-Meteo from the 00/06/12/18z runs",
        "res_km": 9, "grid_deg": 0.081, "default_spacing_deg": 0.1,
        "first_date": date(2017, 1, 1), "delay": "none; updated every 6 h",
    },
    "era5": {
        "label": "ERA5 reanalysis",
        "res_km": 31, "grid_deg": 0.25, "default_spacing_deg": 0.25,
        "first_date": date(1940, 1, 1), "delay": "about 5 days",
    },
}
DEFAULT_MODEL = "ecmwf_ifs"

# What a payload stored BEFORE the model was named (before 2026-09-26) really
# holds. Its label -- "best_match (ERA5 for atmosphere, ERA5-Land for soil)",
# 31 km -- was written by hand; the archive chose the model, and every hour
# checked against named models was ECMWF IFS: 192 of 192 at Kelani and Bangkok
# (2026-09-26), 48 of 48 at Edinburgh, Kelani, Bangkok, Cologne, Houston and
# Sydney (2026-10-26). The same words for every event (BUG-019).
LEGACY_EVIDENCE = ("best_match served ECMWF IFS 9 km in every hour checked against "
                   "named models (192/192 h at Kelani and Bangkok, 2026-09-26; 48/48 h at "
                   "six AOIs on four continents, 2026-10-26)")


def model_of(src: dict) -> dict:
    """The weather source a payload actually rests on, for every renderer.

    `named` is True when the request named the model (the label is exact).
    A legacy payload (no `model_id`) is shown as ECMWF IFS 9 km with the
    evidence, and its stored label is reported as wrong, never shown as fact.
    """
    src = src or {}
    if src.get("model_id"):
        return {"named": True, "id": src["model_id"], "label": src.get("model", src["model_id"]),
                "res_km": src.get("native_res_km"), "note": ""}
    if not src:
        return {"named": False, "id": "", "label": "not recorded", "res_km": None,
                "note": "the payload records no weather source"}
    ifs = MODELS["ecmwf_ifs"]
    return {"named": False, "id": "ecmwf_ifs (not named in the request)",
            "label": f"ECMWF IFS 9 km -- not named in the request; the archive's "
                     f"best_match chose it",
            "res_km": ifs["res_km"],
            "note": f"Stored before the model was named in the request: its label "
                    f"'{src.get('model', '?')}' ({src.get('native_res_km', '?')} km) was "
                    f"written by hand and is wrong. {LEGACY_EVIDENCE[0].upper()}"
                    f"{LEGACY_EVIDENCE[1:]}. Warnings stored with it that cite ERA5's "
                    f"31 km / 0.25 deg grid do not apply."}

# Antecedent Precipitation Index decay. API_t = k*API_(t-1) + P_t, daily.
# 0.90 is the common value for a humid catchment; it is a parameter because it
# is a modelling choice, and it is reported in `derived` for the same reason.
API_K = 0.90

# The hourly fields requested, and how each is aggregated across grid points.
# "mean"/"max"/"min" are ordinary; "p90" is the 90th percentile; "vector_mean"
# is circular; "mode" is the majority value. `frac` thresholds produce the
# area_frac_* series.
SPEC: dict[str, dict[str, Any]] = {
    "precipitation":        {"unit": "mm/h",   "aggs": ["mean", "max", "p90"], "frac": ("gt", 10)},
    "cloud_cover":          {"unit": "%",      "aggs": ["mean", "max", "min"], "frac": ("lt", 20),
                             "out": "cloud_cover_model"},
    "cloud_cover_low":      {"unit": "%",      "aggs": ["mean"]},
    "wind_speed_10m":       {"unit": "m/s",    "aggs": ["mean", "p90", "max"], "frac": ("gt", 5)},
    "wind_gusts_10m":       {"unit": "m/s",    "aggs": ["max"]},
    "wind_direction_10m":   {"unit": "deg",    "aggs": ["vector_mean"]},
    "temperature_2m":       {"unit": "degC",   "aggs": ["mean"]},
    "relative_humidity_2m": {"unit": "%",      "aggs": ["mean"]},
    "pressure_msl":         {"unit": "hPa",    "aggs": ["mean", "min"]},
    "shortwave_radiation":  {"unit": "W/m2",   "aggs": ["mean"]},
    "et0_fao_evapotranspiration": {"unit": "mm", "aggs": ["mean"], "out": "et0_fao"},
    # Soil moisture (soil_moisture_0_to_7cm) was removed on 2026-10-25 (D-6):
    # nothing downstream read it, and wet ground is carried by the antecedent
    # precipitation index. Payloads cached before that keep it, byte-for-byte.
    "is_day":               {"unit": "0/1",    "aggs": ["mode"]},
}


# ---------------------------------------------------------------------------
# small numeric helpers -- each ignores None, and says so by returning None
# ---------------------------------------------------------------------------
def _clean(values: list[Any]) -> list[float]:
    return [float(v) for v in values if v is not None and not _isnan(v)]


def _isnan(v: Any) -> bool:
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def _mean(values: list[Any]) -> float | None:
    good = _clean(values)
    return round(sum(good) / len(good), 4) if good else None


def _pct(values: list[Any], q: float) -> float | None:
    """Linear-interpolation percentile. No numpy: this runs on 4-40 points."""
    good = sorted(_clean(values))
    if not good:
        return None
    if len(good) == 1:
        return round(good[0], 4)
    pos = (len(good) - 1) * q
    low, high = math.floor(pos), math.ceil(pos)
    if low == high:
        return round(good[low], 4)
    return round(good[low] + (good[high] - good[low]) * (pos - low), 4)


def _vector_mean_deg(values: list[Any]) -> float | None:
    """Mean of directions, as a unit-vector resultant.

    The arithmetic mean of 350 and 10 is 180 -- the exact opposite of both.
    """
    good = _clean(values)
    if not good:
        return None
    x = sum(math.cos(math.radians(d)) for d in good)
    y = sum(math.sin(math.radians(d)) for d in good)
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None                       # directions cancelled; no mean exists
    return round(math.degrees(math.atan2(y, x)) % 360.0, 2)


def _mode(values: list[Any]) -> float | None:
    good = _clean(values)
    if not good:
        return None
    return max(set(good), key=good.count)


def _area_frac(values: list[Any], op: str, threshold: float) -> float | None:
    good = _clean(values)
    if not good:
        return None
    hit = sum(1 for v in good if (v > threshold if op == "gt" else v < threshold))
    return round(hit / len(good), 4)


def _complete(row: list[Any]) -> bool:
    """True when every grid point returned a value for this hour.

    The unit of completeness. An hour where some points are missing is NOT
    averaged over the rest -- that would assume the missing points resemble
    the others -- it is a gap, like an hour where all of them are.
    """
    return bool(row) and all(v is not None and not _isnan(v) for v in row)


def _runs(stamps: list[str], idx: list[int]) -> list[list[Any]]:
    """Consecutive indices as `[first_utc, last_utc, hours]` ranges.

    144 missing stamps are unreadable in a report; one range is not.
    """
    out: list[list[Any]] = []
    for i in idx:
        if out and i == out[-1][3] + 1:
            out[-1][1], out[-1][2], out[-1][3] = stamps[i], out[-1][2] + 1, i
        else:
            out.append([stamps[i], stamps[i], 1, i])
    return [r[:3] for r in out]


def _gap_scan(rows: list[list[Any]], stamps: list[str]) -> dict[str, Any]:
    """Which hours of one variable are gaps, over one stretch of time."""
    missing = [i for i, r in enumerate(rows) if not _complete(r)]
    partial = [i for i in missing
               if any(v is not None and not _isnan(v) for v in rows[i])]
    return {"hours": len(rows), "hours_missing": len(missing),
            "partial_hours": len(partial),
            "missing_utc": _runs(stamps, missing)}


def _frange(start: float, stop: float, step: float) -> list[float]:
    """Inclusive float range, rounded to kill accumulated error."""
    out, n = [], 0
    while True:
        value = round(start + n * step, 6)
        if value > stop + 1e-9:
            break
        out.append(value)
        n += 1
    return out or [round(start, 6)]


# ---------------------------------------------------------------------------
# the fetch
# ---------------------------------------------------------------------------
class OpenMeteoError(RuntimeError):
    """The archive refused the request, and said why."""


def _get(url: str) -> tuple[Any, str, int]:
    """JSON body, SHA-256 of the exact bytes, and their length.

    A refusal is raised with Open-Meteo's own `reason`. urllib's HTTPError
    carries only "HTTP Error 400: Bad Request"; the body said "'end_date' is
    out of allowed range from 1940-01-01 to 2026-09-25" on the first Bangkok
    run, and that sentence is the whole diagnosis -- without it Argos'
    debugger has nothing to patch.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": "pralai-flood-research/0.1"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            reason = json.loads(body).get("reason") or body
        except (ValueError, AttributeError):
            reason = body
        # Domain, status and reason only: the URL's query string carries the
        # coordinates and is never logged (core/observability.py).
        observability.source_call(url, ok=False, status=exc.code, reason=reason,
                                  duration_s=time.perf_counter() - t0)
        raise OpenMeteoError(
            f"Open-Meteo refused the request (HTTP {exc.code}): "
            f"{reason.strip()[:500] or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        observability.source_call(url, ok=False, reason=type(exc).__name__,
                                  duration_s=time.perf_counter() - t0)
        raise
    observability.source_call(url, duration_s=time.perf_counter() - t0)
    return (json.loads(raw.decode("utf-8", "replace")),
            hashlib.sha256(raw).hexdigest(), len(raw))


def local_zone(bbox: list[float], first_day: date) -> tuple[float, str, str]:
    """`(offset hours on first_day, IANA zone, how)` for the event, the way
    Module 1 settles it (user bug 2026-11-22: the 0 default labelled UTC as
    local on every console run).

    1. the zone Module 1 (data_availability) stored in the store this run
       writes to -- the session's own, so both modules use one clock;
    2. else Module 1's Open-Meteo zone lookup for the AOI centre;
    3. else the solar offset (longitude / 15), said so.
    The offset is the one in force on `first_day`, from the tz database.
    """
    from .. import dating, experiment

    store = experiment.current()
    zone = dating.store_zone(store) if store is not None else None
    if zone:
        hours = dating.offset_on(zone, first_day)
        if hours is not None:
            return hours, zone, "the zone Module 1 (data availability) stored for this event"
    from .availability import utc_offset

    lat, lon = (bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2
    return utc_offset(lat, lon, first_day)


@tool(
    "fetch_weather",
    "Gridded hourly weather over a bounding box for an event window, "
    "aggregated across the grid (mean/max/p90/area-fraction), with storm "
    "totals, antecedent precipitation index, rain bursts and sensor-gate "
    "inputs. Returns the thesis weather-output schema. `model` is ecmwf_ifs "
    "(default: 9 km, 2017 onwards, no delay) or era5 (31 km, 1940 onwards, "
    "about 5 days behind); best_match is refused. Dates are UTC and may not "
    "be later than today in UTC.",
    label="Event weather (gridded)",
    source="Open-Meteo Archive (ECMWF IFS by default; ERA5 on request)",
    order=10,
    data_source_uncertainty=(
        "What: model output, not observation -- ECMWF IFS at 9 km (ERA5 at 31 km) "
        "smooths convective rain smaller than a grid cell, and its cloud cover is "
        "modelled, not seen by a satellite. Affects: storm totals and rain bursts, "
        "and the cloud and wind values that set the ranking's weights. Reduce: "
        "compare with rain gauges or GPM IMERG; keep IFS rather than ERA5 for small "
        "AOIs."),
    method_uncertainty=(
        "What: statistics over grid points sampled across the bounding box; sea "
        "points are included (about half the Kelani grid, BUG-033) and aoi_mask is "
        "recorded but not applied. Affects: area means and area fractions, which "
        "become the ranking's cloud and wind inputs. Reduce: land-mask the grid "
        "points before aggregating."),
)
def fetch_weather(
    bbox: list[float],
    start_date: str,
    end_date: str,
    event_id: str = "",
    model: str = DEFAULT_MODEL,
    spacing_deg: float | None = None,
    antecedent_days: int = 30,
    local_utc_offset_h: float | None = None,
    aoi_mask: str = "",
    burst_gap_hours: int = 6,
    burst_min_mm: float = 10.0,
    output_dir: str = "outputs",
    write_figure: bool = True,
) -> dict[str, Any]:
    """Event weather over an AOI, to the thesis weather schema.

    Args:
        bbox: [west, south, east, north] in WGS84 degrees.
        start_date, end_date: ISO dates. The window is inclusive of both; the
            fetch silently extends `antecedent_days` earlier so the antecedent
            index has something to integrate, and that extension is reported.
        event_id: names the event in `meta`; free text.
        model: the archive model, named in the request and in the label. One
            of `MODELS`; see the note there on why `best_match` is refused.
        spacing_deg: grid spacing. Defaults to the model's own
            `default_spacing_deg` -- finer interpolates between model nodes
            rather than adding information.
        antecedent_days: lookback for the antecedent precipitation index.
        local_utc_offset_h: for rendering local acquisition times. Storage is
            always UTC. Left out, it is the event's own zone on the window's
            first day (`local_zone`): the zone Module 1 stored for this
            session, else the same Open-Meteo zone lookup Module 1 uses, else
            the solar offset -- and `meta.local_offset_source` says which. It
            used to default to 0, so a console run labelled UTC as local time.
        aoi_mask: path to a GeoJSON that would weight the spatial statistics.
            Recorded but NOT applied -- see the qc warning it raises.
        burst_gap_hours: dry hours that separate one rain burst from the next.
        burst_min_mm: depth below which a burst is not worth reporting.
        output_dir: where the hyetograph goes (default `outputs`).
        write_figure: false to return the payload without drawing it.

    Every hourly variable carries the same aggregate block across grid points.
    `acquisitions` comes back empty; see the module docstring.

    **One request, nothing parallel.** All grid points go to Open-Meteo in a
    single call and nothing here is threaded or async (checked
    2026-10-25). If parallel or async fetching is ever added, it must
    log `parallel` and the user must be told.
    """
    if len(bbox) != 4:
        raise ValueError("bbox must be [west, south, east, north]")
    west, south, east, north = (float(v) for v in bbox)
    if west >= east or south >= north:
        raise ValueError(f"bbox is not west<east, south<north: {bbox}")

    try:
        win_start = date.fromisoformat(start_date)
        win_end = date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError(f"start_date/end_date must be ISO dates: {exc}") from exc
    if win_end < win_start:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")

    fetch_start = win_start - timedelta(days=max(0, antecedent_days))

    # The local clock: stated by the caller, else the event's own zone.
    if local_utc_offset_h is None:
        local_utc_offset_h, local_tz, offset_source = local_zone(bbox, win_start)
    else:
        local_tz, offset_source = "", "stated by the caller"

    if model not in MODELS:
        raise ValueError(
            f"model {model!r} is not one of {sorted(MODELS)}. best_match is "
            "refused on purpose: the server picks IFS, ERA5 or ERA5-Land per "
            "request, so the label could not say which one answered")
    spec_model = MODELS[model]
    if fetch_start < spec_model["first_date"]:
        # The archive answers with nulls before a model's first date rather
        # than an error, and nulls become gaps in every statistic below.
        raise ValueError(
            f"{model} starts {spec_model['first_date']}, but this fetch starts "
            f"{fetch_start} (window {win_start} less {antecedent_days} "
            "antecedent days). Pass model='era5' for an earlier event")
    if spacing_deg is None:
        spacing_deg = spec_model["default_spacing_deg"]

    # -- the grid ----------------------------------------------------------
    lats = _frange(south, north, spacing_deg)
    lons = _frange(west, east, spacing_deg)
    points = [(la, lo) for la in lats for lo in lons]
    if len(points) > 200:
        raise ValueError(
            f"{len(points)} grid points at {spacing_deg} deg over this bbox; "
            "Open-Meteo will refuse the call. Widen spacing_deg or shrink the AOI")

    url = f"{ARCHIVE_URL}?" + urllib.parse.urlencode({
        "latitude": ",".join(f"{la:.4f}" for la, _ in points),
        "longitude": ",".join(f"{lo:.4f}" for _, lo in points),
        "start_date": fetch_start.isoformat(),
        "end_date": win_end.isoformat(),
        "hourly": ",".join(SPEC),
        "models": model,
        "timezone": "UTC",
        # Open-Meteo answers in km/h unless told otherwise, and SPEC labels
        # these series m/s. Without this the numbers are 3.6x too large under
        # an m/s label -- a 22.8 m/s cyclone gust reads as 82.1, and the
        # `wind_speed_10m > 5 m/s` SAR-roughening gate fires on 1.4 m/s air.
        # Nothing downstream could catch it: the values stay plausible-looking
        # and the unit string keeps saying m/s.
        "wind_speed_unit": "ms",
    })
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload, sha, nbytes = _get(url)

    # A single point comes back as an object, several as a list. Normalising
    # here means nothing below has to care how many points were asked for.
    blocks = payload if isinstance(payload, list) else [payload]
    if len(blocks) != len(points):
        raise RuntimeError(
            f"asked for {len(points)} grid points, got {len(blocks)} back")

    times: list[str] = blocks[0].get("hourly", {}).get("time") or []
    if not times:
        raise RuntimeError("the archive returned no hourly series")
    # Open-Meteo returns naive local-to-timezone stamps; timezone=UTC was asked
    # for, so they are UTC instants and are labelled as such rather than left
    # ambiguous in a file someone reads a year from now.
    stamps = [f"{t}Z" if not t.endswith("Z") else t for t in times]

    # -- split antecedent from event --------------------------------------
    window_from = f"{win_start.isoformat()}T00:00Z"
    first = next((i for i, t in enumerate(stamps) if t >= window_from), 0)
    event_stamps = stamps[first:]

    warnings: list[str] = []
    grid_points = []
    for n, ((la, lo), block) in enumerate(zip(points, blocks)):
        grid_points.append({
            "id": n,
            "lat": la,
            "lon": lo,
            "elevation_m": block.get("elevation"),
            # Equal weights. An area weighting would need the AOI polygon, and
            # inventing one from the bbox would be a made-up number dressed as
            # a correction.
            "weight": round(1.0 / len(points), 6),
            "in_aoi": True,
        })

    # -- aggregate each variable across the grid, hour by hour -------------
    series: dict[str, list[list[Any]]] = {}
    missing: dict[str, float] = {}
    for name in SPEC:
        columns = [b.get("hourly", {}).get(name) for b in blocks]
        if any(c is None for c in columns):
            missing[SPEC[name].get("out", name)] = 1.0
            warnings.append(f"{name}: not served for every grid point; skipped")
            continue
        rows = [[col[i] for col in columns] for i in range(len(stamps))]
        series[name] = rows
        gaps = sum(1 for row in rows for v in row if v is None or _isnan(v))
        missing[SPEC[name].get("out", name)] = round(
            gaps / max(1, len(rows) * len(columns)), 4)

    hourly_vars: dict[str, Any] = {}
    for name, rows in series.items():
        spec = SPEC[name]
        out_name = spec.get("out", name)
        block: dict[str, Any] = {"unit": spec["unit"]}
        # An incomplete hour aggregates to None in every statistic: a mean over
        # the points that did report is a guess about the ones that did not.
        event_rows = [r if _complete(r) else None for r in rows[first:]]

        def per_hour(fn):
            return [fn(r) if r is not None else None for r in event_rows]

        for agg in spec["aggs"]:
            if agg == "mean":
                block["mean"] = per_hour(_mean)
            elif agg == "max":
                block["max"] = per_hour(lambda r: max(_clean(r)))
            elif agg == "min":
                block["min"] = per_hour(lambda r: min(_clean(r)))
            elif agg == "p90":
                block["p90"] = per_hour(lambda r: _pct(r, 0.90))
            elif agg == "vector_mean":
                block["vector_mean"] = per_hour(_vector_mean_deg)
            elif agg == "mode":
                block["mode"] = per_hour(_mode)
        if spec.get("frac"):
            op, threshold = spec["frac"]
            key = f"area_frac_{op}_{threshold:g}"
            block[key] = per_hour(lambda r: _area_frac(r, op, threshold))
        hourly_vars[out_name] = block

    # -- gaps: found, listed, never filled ----------------------------------
    gap_vars: dict[str, Any] = {}
    for name, rows in series.items():
        event_scan = _gap_scan(rows[first:], stamps[first:])
        ante_scan = _gap_scan(rows[:first], stamps[:first])
        if event_scan["hours_missing"] or ante_scan["hours_missing"]:
            gap_vars[SPEC[name].get("out", name)] = {
                "event": event_scan, "antecedent": ante_scan}
    not_served = [SPEC[n].get("out", n) for n in SPEC if n not in series]

    summary, withheld = _event_summary(series, stamps, first, points,
                                       win_start, burst_gap_hours,
                                       burst_min_mm)

    # -- qc ----------------------------------------------------------------
    if aoi_mask:
        warnings.append(
            f"aoi_mask {aoi_mask!r} was named but NOT applied -- grid points "
            "are equally weighted. Spatial stats are bbox statistics, not "
            "basin statistics")
    if len(points) < 6:
        warnings.append(
            f"only {len(points)} grid point(s) at {spacing_deg} deg over this "
            "bbox; area fractions are coarse and a CV over so few points is "
            "weak evidence of heterogeneity")
    grid_deg = spec_model["grid_deg"]
    if spacing_deg < grid_deg:
        # Oversampling is allowed -- a small AOI yields few points at native
        # spacing, a thin basis for an area fraction -- but the point count
        # must not be read as independent evidence: the endpoint interpolates
        # between model nodes, so neighbouring points are correlated by
        # construction, the CV understates real sub-grid variability, and an
        # area fraction counts interpolated points.
        #
        # The 2026-09-21 measurement behind this ("four points across 0.3 deg
        # come back distinct") was taken on IFS 9 km data believed to be ERA5,
        # so it showed distinct cells, not interpolation. The warning stands
        # on the general point, not on that measurement.
        cells_lon = max(1, math.ceil((east - west) / grid_deg))
        cells_lat = max(1, math.ceil((north - south) / grid_deg))
        warnings.append(
            f"spacing_deg={spacing_deg} is finer than the {grid_deg} deg "
            f"{model} grid, which spans about {cells_lon * cells_lat} node(s) "
            f"here. The {len(points)} points are not independent: "
            "precipitation_cv understates true sub-grid heterogeneity, and "
            "area fractions count interpolated points rather than model cells")
    warnings.append(
        f"{spec_model['res_km']} km model wind and cloud ({model}) are used to "
        "gate products delivered at 20-250 m; a cell mean cannot resolve what "
        "a scene sees")
    ahead = sum(1 for t in event_stamps if t >= f"{retrieved_at[:16]}Z")
    if ahead:
        # A window reaching "now" ends in hours the model has not reached yet.
        # They are forecast from the latest run and are replaced as later runs
        # arrive, so a refetch can return different numbers for them.
        warnings.append(
            f"{ahead} of the window's {len(event_stamps)} hours are at or after "
            f"the retrieval time {retrieved_at}: they are {model} FORECAST, "
            "not analysed weather, and may differ on a refetch")
    warnings.append(
        "acquisitions[] is empty: satellite overpass times are not available "
        "from Open-Meteo, and the per-scene gates cannot be computed without "
        "them. Wire a scene catalogue before using this for the MCDM")

    cv = None
    if "precipitation" in series:
        if "precipitation" in gap_vars and gap_vars["precipitation"]["event"]["hours_missing"]:
            withheld.append("qc.spatial_heterogeneity.precipitation_cv")
        else:
            totals = [sum(row[n] for row in series["precipitation"][first:])
                      for n in range(len(points))]
            bar = sum(totals) / len(totals) if totals else 0.0
            if bar > 0:
                var = sum((t - bar) ** 2 for t in totals) / len(totals)
                cv = round(math.sqrt(var) / bar, 4)

    if gap_vars or not_served:
        missing_hours = sum(v["event"]["hours_missing"] for v in gap_vars.values())
        warnings.append(
            f"DATA GAPS: {len(gap_vars)} variable(s) have hours with no value "
            f"at one or more grid points ({missing_hours} variable-hours in the "
            f"event window){'; not served at all: ' + ', '.join(not_served) if not_served else ''}. "
            "Gaps are reported in qc.gaps, never filled; "
            f"{len(withheld)} figure(s) that would need the missing hours are "
            "withheld")

    payload: dict[str, Any] = {
        "meta": {
            "event_id": event_id or "unnamed_event",
            "bbox": [west, south, east, north],
            "aoi_mask": aoi_mask or None,
            "window_utc": [f"{win_start.isoformat()}T00:00Z",
                           f"{win_end.isoformat()}T23:00Z"],
            "antecedent_days": antecedent_days,
            "fetch_window_utc": [f"{fetch_start.isoformat()}T00:00Z",
                                 f"{win_end.isoformat()}T23:00Z"],
            "source": {
                "endpoint": ARCHIVE_URL.replace("https://", ""),
                # Written from the request, which names the model, so the
                # label cannot drift from what was served.
                "model": f"{model} -- {spec_model['label']}",
                "model_id": model,
                "native_res_km": spec_model["res_km"],
                "model_delay": spec_model["delay"],
                "retrieved_at": retrieved_at,
                "response_sha256": sha,
                "response_bytes": nbytes,
            },
            "storage_tz": "UTC",
            "local_utc_offset_h": local_utc_offset_h,
            "local_timezone": local_tz,
            "local_offset_source": offset_source,
        },
        "grid": {
            "spacing_deg": spacing_deg,
            "n_points": len(points),
            "points": grid_points,
        },
        "hourly": {"time": event_stamps, "vars": hourly_vars},
        # Empty on purpose. See the module docstring and the qc warning.
        "acquisitions": [],
        "event_summary": summary,
        "derived": [
            {"field": "antecedent_api_mm",
             "method": f"Antecedent Precipitation Index, k={API_K}, daily"},
            {"field": "area_frac_*",
             "method": "fraction of grid points past the threshold, equal weights"},
            {"field": "wind_direction_10m.vector_mean",
             "method": "unit-vector resultant; never an arithmetic mean of angles"},
            {"field": "rain_bursts",
             "method": f"AOI-mean hourly precipitation split on >= "
                       f"{burst_gap_hours} h below 0.1 mm/h, bursts under "
                       f"{burst_min_mm} mm dropped"},
        ],
        "qc": {
            "missing_frac_by_var": {k: v for k, v in missing.items() if v},
            # The gap audit. Present on every payload -- an empty `by_var` is
            # the claim "nothing was missing", which is worth making out loud.
            "gaps": {
                "policy": ("an hour is a gap for a variable when any grid "
                           "point returned no value; gaps are listed here and "
                           "never filled, and a figure that would need a "
                           "missing hour is withheld rather than computed "
                           "over the rest"),
                "window_complete": not any(
                    v["event"]["hours_missing"] for v in gap_vars.values())
                    and not not_served,
                "antecedent_complete": not any(
                    v["antecedent"]["hours_missing"] for v in gap_vars.values())
                    and not not_served,
                "by_var": gap_vars,
                "not_served": not_served,
                "withheld": withheld,
            },
            "points_failed": [],
            "model_fallback": False,
            "spatial_heterogeneity": {"precipitation_cv": cv},
            "warnings": warnings,
        },
    }

    if write_figure and "precipitation" in series:
        from .. import charts

        png = (Path(output_dir) / "weather" / (str(event_id or "") or "event")
               / "hyetograph.png")
        try:
            drawn = charts.hyetograph(payload, png)
            # `figure` is what core/figure_refs.register_run looks for: the
            # chart becomes `<image - run-region-year-hyetograph>` for Argus,
            # in place of the rainfall table.
            payload["figure"] = {"name": "hyetograph", "path": str(png),
                                 "caption": "Rainfall hyetograph: hourly AOI-mean "
                                            "rainfall and its cumulative total "
                                            "over the event window."}
            if drawn["gap_hours"]:
                payload["figure"]["caption"] += (f" {drawn['gap_hours']} missing "
                                                 "hour(s) shaded, not counted.")
        except Exception as exc:                               # noqa: BLE001
            # The weather is the result; the picture of it is not. A chart
            # that cannot be drawn costs the answer its image, never the fetch.
            warnings.append(f"hyetograph not drawn: {type(exc).__name__}: {exc}")
    return payload


def _event_summary(series, stamps, first, points, win_start,
                   burst_gap_hours, burst_min_mm) -> tuple[dict[str, Any], list[str]]:
    """Storm totals, peak intensities, antecedent index and rain bursts.

    Returns `(summary, withheld)`. **Nothing is computed across a gap.** Until
    2026-09-26 the AOI-mean series was built with `_mean(row) or 0.0`, so a
    missing hour entered every total as a dry one: harmless on IFS, which has
    no gaps, and silently low on ERA5 over a recent window, whose last days
    come back empty. Now a figure that needs every hour of its span is None
    when that span has a gap, and its path is named in `withheld`. Where the
    observed hours still bound the answer -- rain that did fall cannot un-fall
    -- the bound is given beside it, labelled as one.
    """
    out: dict[str, Any] = {}
    withheld: list[str] = []
    precip = series.get("precipitation")
    if not precip:
        return ({"note": "precipitation was not returned; no summary computed"},
                ["event_summary"])

    event = precip[first:]
    aoi_mean = [_mean(row) if _complete(row) else None for row in event]
    gaps = [i for i, v in enumerate(aoi_mean) if v is None]
    observed = [v for v in aoi_mean if v is not None]

    if not gaps:
        totals = [sum(row[n] for row in event) for n in range(len(points))]
        out["storm_total_mm"] = {
            "aoi_mean": round(sum(totals) / len(totals), 2) if totals else None,
            "max_point": round(max(totals), 2) if totals else None,
        }
    else:
        out["storm_total_mm"] = {
            "aoi_mean": None,
            "max_point": None,
            "withheld": (f"{len(gaps)} of {len(event)} event hours have no "
                         "complete precipitation; a total over the rest would "
                         "count them as dry"),
            # Rain in the hours that were observed. A floor, never the total.
            "observed_hours_lower_bound_mm": round(sum(observed), 2),
            "observed_hours": len(observed),
        }
        withheld += ["event_summary.storm_total_mm.aoi_mean",
                     "event_summary.storm_total_mm.max_point"]

    # Peak intensity over rolling windows of the AOI mean. With a gap anywhere
    # in the event the true peak may sit inside it, so the peaks are withheld
    # and the best fully observed window is given as a floor.
    peaks: dict[str, Any] = {}
    for hours in (1, 3, 6, 24):
        if len(aoi_mean) < hours:
            continue
        spans = [aoi_mean[i:i + hours] for i in range(len(aoi_mean) - hours + 1)]
        whole = [(sum(w), i) for i, w in enumerate(spans) if None not in w]
        if gaps:
            peaks[f"{hours}h"] = None
            withheld.append(f"event_summary.max_intensity_mm.{hours}h")
            if whole:
                floors = peaks.setdefault("observed_windows_lower_bound", {})
                floors[f"{hours}h"] = round(max(whole)[0], 2)
            continue
        best, at = max(whole, key=lambda pair: pair[0])
        peaks[f"{hours}h"] = round(best, 2)
        if hours == 24:
            peaks["t_max_24h_utc"] = stamps[first + at]
    if gaps and len(aoi_mean) >= 24:
        peaks["t_max_24h_utc"] = None
    out["max_intensity_mm"] = peaks

    # Antecedent Precipitation Index over the days BEFORE the window, so it
    # describes how wet the catchment already was rather than the storm itself.
    # The index is a recursion through every day, so one incomplete day in its
    # tail leaves it unknown.
    before = precip[:first]
    if before:
        daily: dict[str, float] = {}
        incomplete_days: set[str] = set()
        for i, row in enumerate(before):
            day = stamps[i][:10]
            daily.setdefault(day, 0.0)
            if _complete(row):
                daily[day] += _mean(row)
            else:
                incomplete_days.add(day)
        days_sorted = sorted(daily)
        api: dict[str, Any] = {}
        for days in (5, 14, 30):
            tail = days_sorted[-days:] if days <= len(days_sorted) else days_sorted
            broken = sorted(d for d in tail if d in incomplete_days)
            if broken:
                api[f"d{days}"] = None
                api[f"d{days}_withheld"] = (
                    f"{len(broken)} day(s) in its tail have missing hours, "
                    f"{broken[0]} to {broken[-1]}")
                withheld.append(f"event_summary.antecedent_api_mm.d{days}")
            else:
                value = 0.0
                for d in tail:
                    value = API_K * value + daily[d]
                api[f"d{days}"] = round(value, 2)
            if days > len(days_sorted):
                api[f"d{days}_note"] = (f"only {len(days_sorted)} antecedent "
                                        "day(s) fetched; this is not a full lookback")
        out["antecedent_api_mm"] = api
    else:
        out["antecedent_api_mm"] = {
            "note": "antecedent_days=0, so no antecedent index was computed"}

    # Rain bursts: contiguous wet runs separated by a dry gap. Multi-peak
    # separation matters because two 100 mm bursts a day apart and one 200 mm
    # burst are different floods. A missing hour is neither wet nor dry: it
    # ends the run it interrupts, and a burst touching one is flagged, because
    # its true start, end and depth lie partly inside the gap.
    bursts: list[tuple[int, int, bool, bool]] = []
    run: list[int] = []
    dry, after_gap = 0, False
    for i, mm in enumerate(aoi_mean):
        if mm is None:
            if run:
                bursts.append((run[0], run[-1], after_gap, True))
                run = []
            dry, after_gap = 0, True
            continue
        if mm >= 0.1:
            if run and dry >= burst_gap_hours:
                bursts.append((run[0], run[-1], after_gap, False))
                run = []
            if not run and dry > 0:
                after_gap = False        # a dry hour separates it from the gap
            run.append(i)
            dry = 0
        else:
            dry += 1
            if not run:
                after_gap = False
    if run:
        bursts.append((run[0], run[-1], after_gap, False))

    out["rain_bursts"] = []
    for a, b, starts_at_gap, ends_at_gap in bursts:
        depth = sum(aoi_mean[a:b + 1])
        touches = starts_at_gap or ends_at_gap
        if depth < burst_min_mm and not touches:
            continue
        burst: dict[str, Any] = {
            "start_utc": stamps[first + a], "end_utc": stamps[first + b],
            "depth_mm": round(depth, 2),
            "peak_mm_h": round(max(aoi_mean[a:b + 1]), 2)}
        if touches:
            # Kept even under the depth threshold: its depth is a floor, so
            # "too small to report" cannot be known.
            burst["touches_gap"] = {"at_start": starts_at_gap,
                                    "at_end": ends_at_gap}
            burst["depth_is_lower_bound"] = True
        out["rain_bursts"].append(burst)

    if out["rain_bursts"]:
        last_end = out["rain_bursts"][-1]["end_utc"]
        tail = [i for i, t in enumerate(stamps[first:]) if t > last_end]
        if any(aoi_mean[i] is None for i in tail):
            out["dry_hours_after_last_burst"] = None
            withheld.append("event_summary.dry_hours_after_last_burst")
        else:
            out["dry_hours_after_last_burst"] = sum(
                1 for i in tail if aoi_mean[i] < 0.1)
    else:
        out["dry_hours_after_last_burst"] = None

    if "et0_fao_evapotranspiration" in series:
        if peaks.get("t_max_24h_utc"):
            after = [i for i, t in enumerate(stamps[first:])
                     if t >= peaks["t_max_24h_utc"]]
            et0 = series["et0_fao_evapotranspiration"][first:]
            if all(_complete(et0[i]) for i in after):
                out["cum_et0_post_peak_mm"] = round(
                    sum(_mean(et0[i]) for i in after), 2)
            else:
                out["cum_et0_post_peak_mm"] = None
                withheld.append("event_summary.cum_et0_post_peak_mm")
        elif gaps:
            # No peak time, because the peak itself was withheld.
            out["cum_et0_post_peak_mm"] = None
            withheld.append("event_summary.cum_et0_post_peak_mm")

    # Extremes: the one that matters may be in the gap, so a gap withholds.
    for name, key, pick in (("pressure_msl", "min_pressure_msl_hpa", min),
                            ("wind_gusts_10m", "max_gust_ms", max)):
        if name not in series:
            continue
        rows = series[name][first:]
        if all(_complete(r) for r in rows):
            out[key] = round(pick(pick(r) for r in rows), 2) if rows else None
        else:
            out[key] = None
            withheld.append(f"event_summary.{key}")

    return out, withheld
