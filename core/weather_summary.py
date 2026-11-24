"""The weather summarizer: the few values Argus needs to write output 1.

A `fetch_weather` payload carries every hourly series -- 192 hours of a
dozen variables for Kelani, 30 KB and more -- and Argus does not need any of
them to write the weather report. It needs the storm in a dozen numbers, the
completeness verdict, the provenance line, and the hyetograph to show. This
module is that cut, and it is what `core/result_view.py` hands Argus for this
workflow in place of the full document.

    summarize(payload) -> dict      the values, structured
    markdown(summary)  -> str       the same values as the finding Argus reads

**Copied, not recomputed**, with three labelled exceptions that are reductions
of the payload's own hourly AOI means and nothing else: the daily rain totals,
and the window means of cloud and wind. Those two are WEATHER facts about
the event, not the ranking's inputs: since 2026-11-22 the ranking reads cloud
at the optical passes and wind at the radar passes, in local time
(core/pass_weather.py), and its values are in the fact sheet. A missing hour is never counted as dry: a day with one
is withheld, and a window mean over gaps says how many hours it rests on.

The full record stays in the payload (`inputs/weather_grid.json` when the run
names an experiment) and the full document in `reports.weather_markdown`.
"""

from __future__ import annotations

from typing import Any

# A day is listed in full only for windows this short; past it the three
# wettest are enough to write about, and the hyetograph shows the rest.
DAILY_LIST_MAX = 14


def _vars(payload: dict[str, Any]) -> dict[str, Any]:
    return (payload.get("hourly") or {}).get("vars") or {}


def _series(payload: dict[str, Any], var: str, agg: str) -> list[Any]:
    return (_vars(payload).get(var) or {}).get(agg) or []


def _r(value: Any, places: int = 1) -> Any:
    return round(value, places) if isinstance(value, (int, float)) else value


def _window_mean(series: list[Any]) -> dict[str, Any]:
    """Mean over the observed hours, saying how many it rests on."""
    seen = [v for v in series if v is not None]
    return {"value": _r(sum(seen) / len(seen), 2) if seen else None,
            "hours": len(seen), "of": len(series)}


def _daily(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Rain per UTC day from the hourly AOI means; a day with a gap is None."""
    times = (payload.get("hourly") or {}).get("time") or []
    rain = _series(payload, "precipitation", "mean")
    days: dict[str, dict[str, Any]] = {}
    for stamp, value in zip(times, rain):
        row = days.setdefault(stamp[:10], {"day": stamp[:10], "mm": 0.0, "gap": False})
        if value is None:
            row["gap"] = True
        else:
            row["mm"] += value
    return [{"day": d["day"], "mm": None if d["gap"] else round(d["mm"], 1)}
            for d in days.values()]


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    """The values output 1 is written from, as one small dict."""
    meta = payload.get("meta") or {}
    src = meta.get("source") or {}
    grid = payload.get("grid") or {}
    summary = payload.get("event_summary") or {}
    qc = payload.get("qc") or {}
    gaps = qc.get("gaps") or {}
    totals = summary.get("storm_total_mm") or {}
    peaks = summary.get("max_intensity_mm") or {}
    api = summary.get("antecedent_api_mm") or {}
    times = (payload.get("hourly") or {}).get("time") or []

    daily = _daily(payload)
    measured = [d for d in daily if d["mm"] is not None]
    wettest = sorted(measured, key=lambda d: d["mm"], reverse=True)

    gated = sum(1 for f in (_vars(payload).get("wind_speed_10m") or {})
                .get("area_frac_gt_5") or [] if f is not None and f > 0.5)
    cloud = _series(payload, "cloud_cover_model", "mean")
    p90 = [v for v in _series(payload, "wind_speed_10m", "p90") if v is not None]

    bursts = summary.get("rain_bursts") or []
    figure = payload.get("figure") or {}
    from .workflows.weather_grid import model_of

    _model = model_of(src)

    return {
        "event_id": meta.get("event_id"),
        "bbox": meta.get("bbox"),
        "window_utc": meta.get("window_utc"),
        "hours": len(times),
        "provenance": {
            # The source actually served, legacy payloads included (BUG-019).
            "model": _model["label"], "native_res_km": _model["res_km"],
            "model_note": _model["note"],
            "endpoint": src.get("endpoint"), "retrieved_at": src.get("retrieved_at"),
            "response_sha256": (src.get("response_sha256") or "")[:16] or None,
            "grid": f"{grid.get('n_points', '?')} points at "
                    f"{grid.get('spacing_deg', '?')} deg, equally weighted",
            "aoi_mask": meta.get("aoi_mask"),
        },
        "completeness": {
            "audited": "gaps" in qc,
            "window_complete": gaps.get("window_complete"),
            "antecedent_complete": gaps.get("antecedent_complete"),
            "not_served": gaps.get("not_served") or [],
            "withheld": gaps.get("withheld") or [],
        },
        "rain": {
            "storm_total_aoi_mean_mm": _r(totals.get("aoi_mean")),
            "storm_total_wettest_point_mm": _r(totals.get("max_point")),
            "observed_hours_floor_mm": _r(totals.get("observed_hours_lower_bound_mm")),
            "peak_mm": {k: _r(peaks.get(k)) for k in ("1h", "3h", "6h", "24h")
                        if k in peaks},
            "wettest_24h_start_utc": peaks.get("t_max_24h_utc"),
            "antecedent_api_mm": {k: _r(api.get(k)) for k in ("d5", "d14", "d30")
                                  if k in api},
            "bursts": [{"start_utc": b.get("start_utc"), "end_utc": b.get("end_utc"),
                        "depth_mm": _r(b.get("depth_mm")), "peak_mm_h": _r(b.get("peak_mm_h")),
                        "depth_is_floor": bool(b.get("depth_is_lower_bound"))}
                       for b in bursts],
            "dry_hours_after_last_burst": summary.get("dry_hours_after_last_burst"),
            "rain_days_ge_1mm": sum(1 for d in measured if d["mm"] >= 1.0),
            "days_withheld": [d["day"] for d in daily if d["mm"] is None],
            "wettest_days": wettest[:3],
            "daily_mm": daily if len(daily) <= DAILY_LIST_MAX else None,
            "precipitation_cv": (qc.get("spatial_heterogeneity") or {}).get("precipitation_cv"),
        },
        "cloud": {
            "window_mean_pct": _window_mean(cloud),
            "hours_mean_ge_80pct": sum(1 for v in cloud if v is not None and v >= 80),
        },
        "wind": {
            "window_mean_ms": _window_mean(_series(payload, "wind_speed_10m", "mean")),
            "max_p90_ms": _r(max(p90)) if p90 else None,
            "sar_gated_hours": gated,
            "max_gust_ms": _r(summary.get("max_gust_ms")),
        },
        "min_pressure_msl_hpa": _r(summary.get("min_pressure_msl_hpa")),
        "cum_et0_post_peak_mm": _r(summary.get("cum_et0_post_peak_mm")),
        # Only the warnings that change how a number may be read. The rest --
        # the empty acquisitions block, the model resolution -- are standing
        # caveats the report states once.
        "warnings": [w for w in qc.get("warnings") or []
                     if w.startswith("DATA GAPS") or "FORECAST" in w
                     or "NOT applied" in w],
        "figure": figure.get("name") or None,
    }


def _v(value: Any, unit: str = "") -> str:
    return "withheld" if value is None else f"{value}{unit}"


def markdown(s: dict[str, Any]) -> str:
    """The summary as the finding Argus reads: short, labelled, complete."""
    p, c, rain = s["provenance"], s["completeness"], s["rain"]
    window = s.get("window_utc") or ["?", "?"]
    lines = [
        "## Weather summary (output 1)",
        f"- Event `{s.get('event_id')}`, bbox {s.get('bbox')}, "
        f"{window[0]} to {window[1]} UTC, {s['hours']} h.",
        f"- Model: {p['model']}, {p['native_res_km']} km; {p['grid']}; "
        f"retrieved {p['retrieved_at']}, response sha256 {p['response_sha256']}.",
    ]
    if p.get("model_note"):
        lines.append(f"- Source: {p['model_note']}")
    if not c["audited"]:
        lines.append("- Completeness: this payload predates the gap audit.")
    elif c["window_complete"] and c["antecedent_complete"]:
        lines.append("- Completeness: complete -- no missing hour in the window "
                     "or the antecedent period.")
    else:
        lines.append(f"- Completeness: INCOMPLETE (window complete "
                     f"{c['window_complete']}, antecedent complete "
                     f"{c['antecedent_complete']}); withheld: "
                     f"{', '.join(c['withheld']) or 'none'}"
                     + (f"; not served: {', '.join(c['not_served'])}" if c["not_served"] else "")
                     + ".")

    lines += ["", "### Rain"]
    floor = (f" (observed hours alone give at least {rain['observed_hours_floor_mm']} mm)"
             if rain.get("observed_hours_floor_mm") is not None else "")
    lines.append(f"- Storm total, AOI mean: **{_v(rain['storm_total_aoi_mean_mm'], ' mm')}**"
                 f"{floor}; wettest grid point {_v(rain['storm_total_wettest_point_mm'], ' mm')}.")
    if rain["peak_mm"]:
        lines.append("- Peak intensity, AOI mean: "
                     + ", ".join(f"{k} {_v(v, ' mm')}" for k, v in rain["peak_mm"].items())
                     + (f"; wettest 24 h began {rain['wettest_24h_start_utc']}"
                        if rain.get("wettest_24h_start_utc") else "") + ".")
    if rain["antecedent_api_mm"]:
        lines.append("- Antecedent precipitation index: "
                     + ", ".join(f"{k[1:]} d {_v(v, ' mm')}"
                                 for k, v in rain["antecedent_api_mm"].items()) + ".")
    if rain["bursts"]:
        lines.append(f"- Rain bursts ({len(rain['bursts'])}): " + "; ".join(
            f"{b['start_utc']} to {b['end_utc']}, {b['depth_mm']} mm"
            f"{' (a floor)' if b['depth_is_floor'] else ''}, peak {b['peak_mm_h']} mm/h"
            for b in rain["bursts"]) + ".")
    else:
        lines.append("- Rain bursts: none cleared the depth threshold.")
    if rain["daily_mm"] is not None:
        lines.append("- Daily rain (UTC days, from the hourly AOI means): " + ", ".join(
            f"{d['day'][5:]} {_v(d['mm'])}" for d in rain["daily_mm"]) + " mm.")
    else:
        lines.append("- Wettest days (UTC): " + ", ".join(
            f"{d['day']} {d['mm']} mm" for d in rain["wettest_days"]) + ".")
    lines.append(f"- Days with at least 1 mm: {rain['rain_days_ge_1mm']}"
                 + (f"; days withheld for gaps: {', '.join(rain['days_withheld'])}"
                    if rain["days_withheld"] else "")
                 + f". Dry hours after the last burst: "
                   f"{_v(rain['dry_hours_after_last_burst'])}. "
                   f"Precipitation CV across grid points: {_v(rain['precipitation_cv'])}.")

    cloud, wind = s["cloud"], s["wind"]
    cm, wm = cloud["window_mean_pct"], wind["window_mean_ms"]
    lines += ["", "### Cloud, wind, pressure",
              f"- Cloud cover, window mean of the hourly AOI means: "
              f"{_v(cm['value'], ' %')} over {cm['hours']} of {cm['of']} h; "
              f"{cloud['hours_mean_ge_80pct']} h at or above 80 %.",
              f"- 10 m wind, window mean: {_v(wm['value'], ' m/s')} over "
              f"{wm['hours']} of {wm['of']} h; max p90 {_v(wind['max_p90_ms'], ' m/s')}; "
              f"max gust {_v(wind['max_gust_ms'], ' m/s')}; "
              f"{wind['sar_gated_hours']} h with wind over 5 m/s across more than "
              f"half the AOI (the SAR wind-roughening condition).",
              f"- Minimum MSL pressure {_v(s['min_pressure_msl_hpa'], ' hPa')}; "
              f"ET0 after the peak {_v(s['cum_et0_post_peak_mm'], ' mm')}.",
              "- These window means describe the weather; they are NOT the ranking's "
              "`cloud_pct` and `wind_ms`, which are read at the satellite passes in "
              "local time (see the fact sheet's ranking inputs)."]
    if s["warnings"]:
        lines += ["", "### Warnings that change how a number may be read"]
        lines += [f"- {w}" for w in s["warnings"]]
    lines += ["", "### Hyetograph",
              ("- Drawn: hourly AOI-mean rainfall with the cumulative line, bursts "
               "shaded, wettest 24 h bracketed. Show it with its placeholder from "
               "the figure list." if s.get("figure") else
               "- Not drawn for this payload.")]
    lines += ["", "The hourly series are withheld from this summary; the full "
              "record is in this run's monty_result."]
    return "\n".join(lines)
