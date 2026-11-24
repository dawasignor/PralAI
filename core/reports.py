"""Rendering a cached experiment payload into a readable thesis output.

The payload is the record and the markdown is the reading of it. Both exist
because they answer different questions: `inputs/weather_grid.json` is what a
number can be traced back to, and `01_weather_data.md` is what a person can
actually read.

**Nothing here recomputes anything.** Every figure is copied from the payload,
so the document and the record cannot disagree -- and a figure that is missing
from the payload is rendered as missing rather than filled in. A renderer that
quietly derived one more statistic would be a second, unversioned source of
thesis numbers.
"""

from __future__ import annotations

from typing import Any



def _f(value: Any, places: int = 2, unit: str = "") -> str:
    """A number for a table cell, or an explicit gap."""
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        return f"{value:,.{places}f}{unit}"
    return str(value)


def _series(payload: dict, var: str, agg: str) -> list[Any]:
    block = payload.get("hourly", {}).get("vars", {}).get(var) or {}
    return block.get(agg) or []


def _daily_rollup(payload: dict) -> list[dict[str, Any]]:
    """One row per UTC day, from the hourly aggregates already computed."""
    times = payload.get("hourly", {}).get("time") or []
    rain = _series(payload, "precipitation", "mean")
    rain_max = _series(payload, "precipitation", "max")
    cloud = _series(payload, "cloud_cover_model", "mean")
    wind = _series(payload, "wind_speed_10m", "mean")
    wind_p90 = _series(payload, "wind_speed_10m", "p90")
    gust = _series(payload, "wind_gusts_10m", "max")
    frac = (payload.get("hourly", {}).get("vars", {})
            .get("wind_speed_10m", {}).get("area_frac_gt_5") or [])

    days: dict[str, dict[str, Any]] = {}
    for i, stamp in enumerate(times):
        day = stamp[:10]
        row = days.setdefault(day, {"day": day, "rain": 0.0, "peak_1h": 0.0,
                                    "cloud": [], "wind": [], "wind_p90": [],
                                    "gust": 0.0, "gated": 0, "hours": 0,
                                    "missing": 0})
        row["hours"] += 1
        # A None hour is a gap, not a dry or calm hour. Summing past it would
        # turn a missing hour into 0 mm -- the day's figures are withheld
        # instead, and the count of missing hours is shown in their place.
        if any(i < len(s) and s[i] is None
               for s in (rain, cloud, wind, wind_p90, gust)):
            row["missing"] += 1
        if i < len(rain) and rain[i] is not None:
            row["rain"] += rain[i]
        if i < len(rain_max) and rain_max[i] is not None:
            row["peak_1h"] = max(row["peak_1h"], rain_max[i])
        if i < len(cloud) and cloud[i] is not None:
            row["cloud"].append(cloud[i])
        if i < len(wind) and wind[i] is not None:
            row["wind"].append(wind[i])
        if i < len(wind_p90) and wind_p90[i] is not None:
            row["wind_p90"].append(wind_p90[i])
        if i < len(gust) and gust[i] is not None:
            row["gust"] = max(row["gust"], gust[i])
        if i < len(frac) and frac[i] and frac[i] > 0.5:
            row["gated"] += 1

    out = []
    for row in days.values():
        whole = row["missing"] == 0
        out.append({
            "day": row["day"],
            "rain_mm": round(row["rain"], 1) if whole else None,
            "peak_1h": round(row["peak_1h"], 1) if whole else None,
            "cloud_mean": round(sum(row["cloud"]) / len(row["cloud"]), 0)
                          if row["cloud"] and whole else None,
            "wind_mean": round(sum(row["wind"]) / len(row["wind"]), 1)
                         if row["wind"] and whole else None,
            "wind_p90_max": (round(max(row["wind_p90"]), 1)
                             if row["wind_p90"] and whole else None),
            "gust_max": round(row["gust"], 1) if whole else None,
            "gated_hours": row["gated"],
            "hours": row["hours"],
            "missing_hours": row["missing"],
        })
    return out


def _gap_section(payload: dict[str, Any]) -> list[str]:
    """The completeness audit: what is missing, and what that withholds.

    Rendered from `qc.gaps`, which the workflow writes on every payload since
    2026-09-26. An older payload has only `qc.missing_frac_by_var`; that is
    reported for what it is, and an older payload WITH missing values is
    flagged, because its summary was computed with gaps counted as 0 mm.
    """
    qc = payload.get("qc") or {}
    gaps = qc.get("gaps")
    lines = ["## Uncertainty: data completeness", ""]
    add = lines.append
    add("Gaps are **reported, never filled**. An hour counts for a variable "
        "only if every grid point returned a value; a figure that would need a "
        "missing hour is withheld rather than computed over the rest, and "
        "shown as *withheld* wherever it would appear below.")
    add("")

    if gaps is None:
        missing = qc.get("missing_frac_by_var") or {}
        if not missing:
            add("**No missing values.** This payload predates the per-hour gap "
                "audit (2026-09-26), but its qc records no missing value in any "
                "requested variable, so nothing below rests on a filled hour.")
        else:
            add("**This payload predates the gap audit and HAS missing "
                "values:** "
                + ", ".join(f"`{k}` {v:.1%}" for k, v in missing.items())
                + ". It was summarised by code that counted a missing hour as "
                "0 mm, so its storm totals, peaks and antecedent index may be "
                "LOW. Re-fetch before citing any of them.")
        add("")
        return lines

    by_var = gaps.get("by_var") or {}
    not_served = gaps.get("not_served") or []
    if not by_var and not not_served:
        add("**Complete.** Every grid point returned a value for every hour of "
            "the event window and of the antecedent period, in every variable. "
            "Nothing below rests on a filled or assumed value.")
        add("")
        return lines

    add(f"**Incomplete.** Event window complete: "
        f"**{gaps.get('window_complete')}**; antecedent period complete: "
        f"**{gaps.get('antecedent_complete')}**.")
    add("")
    if by_var:
        add("| Variable | Event hours missing | of which partial | Missing (UTC, event) "
            "| Antecedent hours missing |")
        add("|---|---|---|---|---|")
        for name, entry in by_var.items():
            ev, an = entry.get("event") or {}, entry.get("antecedent") or {}
            spans = "; ".join(f"{a} to {b} ({n} h)" for a, b, n in
                              ev.get("missing_utc") or []) or "--"
            add(f"| `{name}` | {ev.get('hours_missing', 0)} of {ev.get('hours', '?')} "
                f"| {ev.get('partial_hours', 0)} | {spans} "
                f"| {an.get('hours_missing', 0)} of {an.get('hours', '?')} |")
        add("")
        add("*Partial* hours are ones where some grid points reported and "
            "others did not. They are gaps too: a mean over the points that "
            "reported is a guess about the ones that did not.")
        add("")
    if not_served:
        add(f"Not served at all: {', '.join(f'`{n}`' for n in not_served)}.")
        add("")
    withheld = gaps.get("withheld") or []
    if withheld:
        add("**Withheld because of these gaps:**")
        add("")
        for path in withheld:
            add(f"- `{path}`")
        add("")
    return lines


def _w(value: Any, places: int = 1, unit: str = "") -> str:
    """Like `_f`, but a None in a gap-audited payload is a withheld figure."""
    return "*withheld (gaps)*" if value is None else _f(value, places) + unit


def weather_markdown(payload: dict[str, Any]) -> str:
    """The weather output document, rendered from a cached payload."""
    meta = payload.get("meta", {})
    src = meta.get("source", {})
    grid = payload.get("grid", {})
    summary = payload.get("event_summary", {})
    qc = payload.get("qc", {})
    bbox = meta.get("bbox") or [None] * 4
    window = meta.get("window_utc") or ["?", "?"]

    lines: list[str] = []
    add = lines.append

    add("## What this is")
    add("")
    add("Hourly weather over the AOI for the event window, sampled on a grid "
        "and aggregated across it. Two things it is for: the meteorological "
        "description of the flood, and the **sensor gates** -- whether the "
        "weather at a satellite overpass was the kind that breaks a flood map.")
    add("")
    add("The full hourly series is in `inputs/weather_grid.json`, which is the "
        "record. Everything below is copied from it; nothing here is "
        "recomputed.")
    add("")

    # -- provenance --------------------------------------------------------
    add("## Provenance")
    add("")
    add("| Field | Value |")
    add("|---|---|")
    add(f"| Event | `{meta.get('event_id', '?')}` |")
    add(f"| AOI bbox (W,S,E,N) | {_f(bbox[0], 3)}, {_f(bbox[1], 3)}, "
        f"{_f(bbox[2], 3)}, {_f(bbox[3], 3)} |")
    add(f"| Window (UTC) | {window[0]} to {window[1]} |")
    add(f"| Fetched window | {(meta.get('fetch_window_utc') or ['?'])[0]} "
        f"onwards ({meta.get('antecedent_days', '?')} d antecedent) |")
    add(f"| Endpoint | `{src.get('endpoint', '?')}` |")
    from .workflows.weather_grid import model_of

    model = model_of(src)           # the source actually served (BUG-019)
    add(f"| Model | {model['label']} |")
    add(f"| Native resolution | {model['res_km'] or '?'} km |")
    add(f"| Retrieved | {src.get('retrieved_at', '?')} |")
    add(f"| Response SHA-256 | `{src.get('response_sha256', '?')}` |")
    add(f"| Response size | {_f(src.get('response_bytes'), 0)} bytes |")
    add(f"| Storage timezone | {meta.get('storage_tz', '?')} "
        f"(local offset {meta.get('local_utc_offset_h', '?')} h"
        + (f", {meta['local_timezone']}" if meta.get("local_timezone") else "")
        + (f"; {meta['local_offset_source']}" if meta.get("local_offset_source") else "")
        + ") |")
    add(f"| AOI mask | {meta.get('aoi_mask') or 'none -- bbox statistics, not basin statistics'} |")
    add("")
    if model["note"]:
        add(f"> **Source.** {model['note']}")
        add("")
    add("The SHA-256 is of the exact response bytes. It is the receipt: a "
        "figure in the thesis can be traced to a payload, and that payload to "
        "one request.")
    add("")

    # -- grid --------------------------------------------------------------
    add("## Sampling grid")
    add("")
    add(f"{grid.get('n_points', '?')} points at {grid.get('spacing_deg', '?')} "
        "degree spacing, equally weighted.")
    add("")
    points = grid.get("points") or []
    if points:
        add("| id | lat | lon | elevation (m) | weight |")
        add("|---|---|---|---|---|")
        for p in points:
            add(f"| {p.get('id')} | {_f(p.get('lat'), 3)} | "
                f"{_f(p.get('lon'), 3)} | {_f(p.get('elevation_m'), 0)} | "
                f"{_f(p.get('weight'), 4)} |")
        add("")

    # -- completeness, before any number is read ---------------------------
    lines.extend(_gap_section(payload))
    # Only a payload that carries the audit may render None as "withheld";
    # an older one's None means what it always did, an explicit gap.
    fmt = _w if qc.get("gaps") is not None else (
        lambda v, places=1, unit="": _f(v, places) + ("" if v is None else unit))

    # -- the event --------------------------------------------------------
    add("## Event summary")
    add("")
    totals = summary.get("storm_total_mm") or {}
    peaks = summary.get("max_intensity_mm") or {}
    api = summary.get("antecedent_api_mm") or {}
    add("| Quantity | Value |")
    add("|---|---|")
    add(f"| Storm total, AOI mean | **{fmt(totals.get('aoi_mean'), 1, ' mm')}** |")
    add(f"| Storm total, wettest grid point | {fmt(totals.get('max_point'), 1, ' mm')} |")
    if "observed_hours_lower_bound_mm" in totals:
        add(f"| Rain in the {totals.get('observed_hours')} observed hours "
            f"(a floor, not the total) | at least "
            f"{_f(totals['observed_hours_lower_bound_mm'], 1)} mm |")
    for key in ("1h", "3h", "6h", "24h"):
        if key in peaks:
            add(f"| Max {key} intensity (AOI mean) | {fmt(peaks[key], 1, ' mm')} |")
    for key, value in (peaks.get("observed_windows_lower_bound") or {}).items():
        add(f"| Max {key}, fully observed windows only (a floor) | "
            f"at least {_f(value, 1)} mm |")
    if peaks.get("t_max_24h_utc"):
        add(f"| Wettest 24 h began | {peaks['t_max_24h_utc']} |")
    for key in ("d5", "d14", "d30"):
        if key in api:
            add(f"| Antecedent API, {key[1:]} d | {fmt(api[key], 1, ' mm')} |")
    add(f"| Minimum MSL pressure | {fmt(summary.get('min_pressure_msl_hpa'), 1, ' hPa')} |")
    add(f"| Maximum gust | {fmt(summary.get('max_gust_ms'), 1, ' m/s')} |")
    add(f"| Dry hours after last burst | {_f(summary.get('dry_hours_after_last_burst'), 0)} |")
    add(f"| Cumulative ET0 after the peak | {fmt(summary.get('cum_et0_post_peak_mm'), 1, ' mm')} |")
    add("")

    bursts = summary.get("rain_bursts") or []
    add(f"### Rain bursts ({len(bursts)})")
    add("")
    if bursts:
        add("| Start (UTC) | End (UTC) | Depth (mm) | Peak (mm/h) | Touches a gap |")
        add("|---|---|---|---|---|")
        for b in bursts:
            touch = b.get("touches_gap")
            flag = ("--" if not touch else
                    " and ".join(side for side, hit in
                                 (("start", touch.get("at_start")),
                                  ("end", touch.get("at_end"))) if hit)
                    + " -- depth is a floor")
            add(f"| {b.get('start_utc')} | {b.get('end_utc')} | "
                f"{_f(b.get('depth_mm'), 1)} | {_f(b.get('peak_mm_h'), 1)} | {flag} |")
        add("")
        add("Bursts are separated so a multi-peak storm is not read as one "
            "block: two 100 mm bursts a day apart and one 200 mm burst are "
            "different floods and wet the catchment differently.")
    else:
        add("_No burst cleared the depth threshold._")
    add("")

    # -- rainfall, as a graph ----------------------------------------------
    # The hyetograph replaces the per-day rainfall columns (user decision,
    # 2026-10-14): a storm's shape -- when it rained, how hard, in how many
    # bursts -- is what a table of daily sums hides. The numbers it is drawn
    # from are the event summary above; the chart adds none.
    figure = payload.get("figure") or {}
    add("## Rainfall hyetograph")
    add("")
    if figure.get("name") == "hyetograph" and figure.get("path"):
        add(f"![Rainfall hyetograph]({str(figure['path']).replace(chr(92), '/')})")
        add("")
        add(f"*{figure.get('caption', '')}* Bars are the hourly rainfall averaged "
            "over the grid, the line the rain accumulated since the window "
            "opened; rain bursts are shaded with their depth and the wettest "
            "24 h is bracketed. A missing hour is shaded grey and the line stops "
            "there rather than counting it as dry.")
    else:
        add("_Not drawn for this payload._ It predates the hyetograph "
            "(2026-10-14) or was fetched with `write_figure=false`; re-fetch, or "
            "draw it from this payload with `core.charts.hyetograph`.")
    add("")

    # -- daily ------------------------------------------------------------
    add("## Daily rollup")
    add("")
    add("Cloud and wind per day. Rainfall is in the hyetograph above.")
    add("")
    add("`gated h` counts hours where 10 m wind exceeded 5 m/s over more than "
        "half the AOI -- the SAR wind-roughening condition. It is a count of "
        "hours, not a verdict on any scene: no acquisition times are in this "
        "output.")
    add("")
    add("A day with a missing hour shows `--` in place of its means: they "
        "would treat the missing hours as calm or clear.")
    add("")
    add("| Day (UTC) | Cloud mean (%) | Wind mean (m/s) "
        "| Wind p90 max | Gust max | gated h | missing h |")
    add("|---|---|---|---|---|---|---|")
    for row in _daily_rollup(payload):
        add(f"| {row['day']} | {_f(row['cloud_mean'], 0)} | {_f(row['wind_mean'], 1)} "
            f"| {_f(row['wind_p90_max'], 1)} | {_f(row['gust_max'], 1)} "
            f"| {row['gated_hours']}/{row['hours']} | {row['missing_hours']} |")
    add("")

    # -- what is measured vs derived --------------------------------------
    add("## Derived, not measured")
    add("")
    for entry in payload.get("derived") or []:
        add(f"- **`{entry.get('field')}`** -- {entry.get('method')}")
    add("")

    # -- acquisitions ------------------------------------------------------
    acqs = payload.get("acquisitions")
    add("## Acquisitions")
    add("")
    if acqs:
        add(f"{len(acqs)} acquisition(s) matched to the hourly series.")
    else:
        add("**Empty.** This block pairs each candidate product's overpass "
            "with the weather at that instant, and computes the per-scene "
            "gates from it. Open-Meteo does not know when Sentinel-1 flew, so "
            "the acquisition times are not available here and the gates are "
            "not computed.")
        add("")
        add("It is left empty rather than filled with plausible times, because "
            "a gate is a claim about a specific scene and an invented "
            "overpass would make that claim unfalsifiable. **A scene "
            "catalogue has to be wired in before this output can feed the "
            "MCDM.**")
    add("")

    # -- qc ----------------------------------------------------------------
    add("## Quality control")
    add("")
    het = qc.get("spatial_heterogeneity") or {}
    add(f"- Precipitation CV across grid points: "
        f"**{_f(het.get('precipitation_cv'), 4)}**")
    missing = qc.get("missing_frac_by_var") or {}
    if missing:
        add("- Missing fractions: "
            + ", ".join(f"`{k}` {v:.1%}" for k, v in missing.items()))
    else:
        add("- No missing values in any requested variable.")
    add(f"- Grid points that failed: {qc.get('points_failed') or 'none'}")
    add(f"- Model fallback: {qc.get('model_fallback')}")
    add("")
    add("### Warnings")
    add("")
    for warning in qc.get("warnings") or []:
        add(f"- {warning}")
    add("")
    return "\n".join(lines)
