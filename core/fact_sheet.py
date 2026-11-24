"""The session fact sheet: what the agents know, kept on disk.

`sessions/<id>/facts.md` is rebuilt from the session's stored results
(core/session_store.py) every time one is added, and read from disk into every
agent's prompt. It replaces context assembled on the fly from whatever the
current turn happened to hold -- which is how a ranking turn reported the five
event indices "not present in the session" when the data behind every one of
them had been fetched on earlier turns.

Written for agents, not people: key: value lines, minimal phrases, one section
per result, a figure always with its denominator or unit. **Every value is
copied from a stored result**, with one exception computed by the function the
ranking itself uses (`mcdm.indices_from_payloads`): the five ranking indices,
so the value Argus passes is the value the thesis would compute.

A section whose data is not stored says MISSING and names the workflow that
would produce it. Nothing is estimated to fill a gap.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE = "facts.md"

# Stored-result name -> (plain label, workflow, thesis output).
STEPS: list[tuple[str, str, str, str]] = [
    ("weather_grid", "Weather", "fetch_weather", "module 2"),
    ("lulc", "Land cover", "fetch_lulc_data", ""),
    ("terrain", "Terrain", "fetch_terrain_data", ""),
    ("permanent_water", "Permanent water", "fetch_permanent_water", ""),
    ("data_availability", "Product availability", "data_availability", ""),
    ("rank_flood_products", "Product ranking", "rank_flood_products", "modules 6-7"),
    ("sensitivity_analysis", "Sensitivity", "sensitivity_analysis", "module 8"),
    ("accuracy_metrics", "Agreement with reference", "accuracy_metrics", ""),
    # place_context: retired with fetch_place_context on 2026-10-25 (ledger B4).
    ("worldcover", "Land cover fallback (WorldCover 2021)", "fetch_worldcover", ""),
]

# Cloud and wind are read at Module 1's pass times (2026-11-22), so they need
# the availability check as well as the weather.
INDEX_NEEDS = {"cloud_pct": ("weather_grid", "data_availability"),
               "wind_ms": ("weather_grid", "data_availability"),
               "veg_pct": ("lulc",), "steep_pct": ("terrain",), "urban_pct": ("lulc",)}

# veg_pct and urban_pct may come from WorldCover instead, when Sentinel-2 is
# on record as having failed its threshold at the defaults.
FALLBACK_OF = {"veg_pct": "share of classified land, WorldCover 2021 tree cover",
               "urban_pct": "share of classified land, WorldCover 2021 built-up"}

# What each index is a share or mean OF, as mcdm.indices_from_payloads states.
INDEX_OF = {"cloud_pct": "mean AOI cloud at the optical passes, local time",
            "wind_ms": "mean AOI 10 m wind at the radar passes, local time",
            "veg_pct": "share of OBSERVED (cloud-free) land",
            "steep_pct": "share of all land",
            "urban_pct": "share of OBSERVED (cloud-free) land, BUI > threshold"}


def _get(data: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _n(value: Any, places: int = 2) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"{value:.{places}f}".rstrip("0").rstrip(".") if places else f"{value:.0f}"
    return str(value)


def _pct(fraction: Any) -> str:
    return "--" if fraction is None else _n(float(fraction) * 100, 1)


def _payloads(store: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for name, *_ in STEPS:
        record = store.cached_input(name) if store is not None else None
        if record and isinstance(record.get("payload"), dict):
            out[name] = record
    return out


def _lulc_failure(store: Any) -> dict[str, Any] | None:
    """The recorded Sentinel-2 failure for this AOI, if there is one."""
    getter = getattr(store, "failure", None)
    return getter("lulc") if callable(getter) else None


# -- one section per stored result --------------------------------------------
def _weather(p: dict[str, Any]) -> list[str]:
    from .weather_summary import summarize

    s = summarize(p)
    rain, c, prov = s["rain"], s["completeness"], s["provenance"]
    peaks = rain["peak_mm"]
    api = rain["antecedent_api_mm"]
    complete = ("complete" if c["window_complete"] and c["antecedent_complete"]
                else "INCOMPLETE; withheld: " + (", ".join(c["withheld"]) or "none")
                if c["audited"] else "not audited (old result)")
    lines = [
        f"model: {prov['model']}, {prov['native_res_km']} km | grid: {prov['grid']}",
        f"window_utc: {(s.get('window_utc') or ['?', '?'])[0]} to {(s.get('window_utc') or ['?', '?'])[1]} ({s['hours']} h)",
        f"storm_total_mm: {_n(rain['storm_total_aoi_mean_mm'], 1)} AOI mean | "
        f"{_n(rain['storm_total_wettest_point_mm'], 1)} wettest point",
        "peak_mm: " + " | ".join(f"{k} {_n(v, 1)}" for k, v in peaks.items())
        + (f" | wettest 24 h from {rain['wettest_24h_start_utc']}"
           if rain.get("wettest_24h_start_utc") else ""),
        "antecedent_api_mm: " + (" | ".join(f"{k[1:]} d {_n(v, 1)}" for k, v in api.items()) or "--"),
        f"rain_bursts: {len(rain['bursts'])}"
        + (f" | largest {_n(max(b['depth_mm'] or 0 for b in rain['bursts']), 1)} mm"
           if rain["bursts"] else ""),
        f"cloud_mean_pct: {_n(s['cloud']['window_mean_pct']['value'])} "
        f"({s['cloud']['window_mean_pct']['hours']}/{s['cloud']['window_mean_pct']['of']} h)",
        f"wind_mean_ms: {_n(s['wind']['window_mean_ms']['value'])} | "
        f"max_gust_ms: {_n(s['wind']['max_gust_ms'], 1)} | "
        f"sar_wind_gated_h: {s['wind']['sar_gated_hours']}",
        f"completeness: {complete}",
    ]
    lines += [f"warning: {w[:160]}" for w in s["warnings"]]
    return lines


def _lulc(p: dict[str, Any]) -> list[str]:
    classes = _get(p, "indices.ndvi.classes.fractions") or {}
    bui = _get(p, "indices.bui.stats") or {}
    scene = p.get("scene") or {}
    return [
        f"scene: {str(scene.get('datetime') or '--')[:10]} "
        f"({_n(scene.get('days_before_start'), 0)} d before window) | "
        f"mosaic: {_n(scene.get('is_mosaic'))}",
        f"valid_fraction_of_land: {_n(scene.get('aoi_valid_fraction'), 4)} (cloud-free share)",
        f"land_km2: {_n(_get(p, 'aoi.land_area_km2'), 1)}",
        "ndvi_classes_pct_of_observed_land: "
        + (" | ".join(f"{k} {_pct(v)}" for k, v in classes.items()) or "--"),
        f"bui_mean: {_n(bui.get('mean'), 3)}",
    ]


def _terrain(p: dict[str, Any]) -> list[str]:
    elev = _get(p, "elevation.stats") or {}
    slope = _get(p, "slope.classes.fractions") or {}
    return [
        f"elevation_m: mean {_n(elev.get('mean'), 1)} | min {_n(elev.get('min'), 1)} | "
        f"max {_n(elev.get('max'), 1)}",
        f"slope_deg: mean {_n(_get(p, 'slope.stats.mean'), 2)} | p90 {_n(_get(p, 'slope.stats.p90'), 2)}",
        "slope_classes_pct_of_land: "
        + (" | ".join(f"{k} {_pct(v)}" for k, v in slope.items()) or "--"),
        f"land_km2: {_n(_get(p, 'aoi.land_area_km2'), 1)}",
    ]


def _water(p: dict[str, Any]) -> list[str]:
    water = p.get("water") or {}
    return [
        " | ".join(f"{k}_km2: {_n(_get(water, k + '.area_km2'), 2)}"
                   for k in ("permanent", "seasonal", "ephemeral")),
        f"thresholds_pct_occurrence: permanent >= {_n(_get(p, 'thresholds.permanent'), 0)} | "
        f"seasonal >= {_n(_get(p, 'thresholds.seasonal'), 0)}",
        f"land_km2: {_n(_get(p, 'aoi.land_area_km2'), 1)}",
    ]


def _availability(p: dict[str, Any]) -> list[str]:
    period = p.get("period") or {}
    lines = [f"period_local: {period.get('start', '?')} to {period.get('end', '?')}"]
    for product in p.get("products") or []:
        t = product.get("totals") or {}
        state = ("search failed" if product.get("status") == "unreachable"
                 else f"{t.get('instances', 0)} acquisitions on {t.get('days_available', 0)} d"
                 + (f" ({t['days_in_gibs']} d read from GIBS daily composites; "
                    f"{t.get('days_all_cloud', 0)} d all cloud, counted 0)"
                    if t.get("days_in_gibs") else "")
                 + (f"; {t['days_not_checkable']} d no data"
                    if t.get("days_not_checkable") else ""))
        lines.append(f"{product.get('label', product.get('id'))}: {state}")
    return lines


def _ranking(p: dict[str, Any]) -> list[str]:
    rows = sorted(p.get("ranking") or [], key=lambda r: r.get("rank", 99))
    return ([f"indices_used: " + " | ".join(f"{k} {_n(v, 4)}" for k, v in
                                           (p.get("indices") or {}).items())]
            + [f"rank {r.get('rank')}: {r.get('alternative')} ({r.get('sensor')}) "
               f"closeness {_n(r.get('closeness'), 3)}" for r in rows])


def _sensitivity(p: dict[str, Any]) -> list[str]:
    critical = p.get("most_critical") or {}
    return [
        f"leader: {_get(p, 'baseline.leader') or '--'}",
        f"most_critical: {critical.get('criterion', '--')} {critical.get('name', '')} | "
        f"weight change to flip {_n(critical.get('delta'), 4)}",
        f"stable_criteria: {', '.join(p.get('stable_criteria') or []) or 'none'}",
    ]


def _worldcover(p: dict[str, Any]) -> list[str]:
    fractions = _get(p, "classes.fractions") or {}
    return [
        f"vintage: {str(_get(p, 'source.time_start') or '--')[:10]} to "
        f"{str(_get(p, 'source.time_end') or '--')[:10]} (a snapshot, not the event)",
        f"valid_fraction_of_land: {_n(_get(p, 'coverage.valid_fraction'), 4)}",
        f"land_km2: {_n(_get(p, 'aoi.land_area_km2'), 1)}",
        "classes_pct_of_land: "
        + (" | ".join(f"{k} {_pct(v)}" for k, v in fractions.items() if v) or "--"),
        f"overall_accuracy_pct: {_n(_get(p, 'accuracy.overall_pct'), 1)} (global, published)",
    ]


SECTIONS = {"weather_grid": _weather, "lulc": _lulc, "terrain": _terrain,
            "worldcover": _worldcover,
            "permanent_water": _water, "data_availability": _availability,
            "rank_flood_products": _ranking, "sensitivity_analysis": _sensitivity}


def _indices(payloads: dict[str, dict[str, Any]],
             lulc_failure: dict[str, Any] | None = None) -> list[str]:
    """The five ranking inputs, computed the ranking's own way, or what is missing.

    Land cover is Sentinel-2 when it is stored. Without it, the WorldCover
    fallback counts only when Sentinel-2 is on record as having failed its
    0.7 threshold at the defaults; the sheet then says which source each index
    came from. Anything less is MISSING, with the reason.
    """
    from .workflows.mcdm import indices_from_payloads, s2_failed_at_defaults

    names = {n: w for n, _, w, _ in STEPS}
    fallback = False
    fallback_note = ""
    if "lulc" not in payloads and "worldcover" in payloads:
        bbox = _get(payloads["worldcover"]["payload"], "aoi.bbox")
        fallback, fallback_note = s2_failed_at_defaults(lulc_failure, bbox)
    have = set(payloads) | ({"lulc"} if fallback else set())
    missing = sorted({n for needs in INDEX_NEEDS.values() for n in needs if n not in have})
    if missing:
        lines = ["status: MISSING -- needs " + ", ".join(names[m] for m in missing)]
        if "lulc" in missing and "worldcover" in payloads:
            lines.append(f"land_cover_fallback: not usable -- {fallback_note}")
        lines += [f"{k}: -- (needs {', '.join(names[n] for n in v if n in missing)})"
                  if set(v) & set(missing) else f"{k}: available once complete"
                  for k, v in INDEX_NEEDS.items()]
        return lines

    try:
        out = indices_from_payloads(
            payloads["weather_grid"]["payload"],
            None if fallback else payloads["lulc"]["payload"],
            payloads["terrain"]["payload"],
            availability=payloads["data_availability"]["payload"],
            worldcover=payloads["worldcover"]["payload"] if fallback else None,
            lulc_failure=lulc_failure if fallback else None)
    except Exception as exc:                                   # noqa: BLE001
        return [f"status: ERROR computing from stored data -- {type(exc).__name__}: {exc}"[:240]]
    lines = ["status: READY -- pass these exact values as params to "
             "rank_flood_products and sensitivity_analysis"]
    if fallback:
        lines.append("land_cover_source: fetch_worldcover (FALLBACK -- Sentinel-2 "
                     "failed its 0.7 threshold at the defaults; WorldCover is a "
                     "2021 snapshot; say so wherever veg_pct or urban_pct is used)")
    else:
        lines.append("land_cover_source: fetch_lulc_data (Sentinel-2)")
    sources = out.get("index_source") or {}
    for key, value in out["indices"].items():
        of = FALLBACK_OF.get(key) if fallback and key in FALLBACK_OF else INDEX_OF.get(key, "")
        lines.append(f"{key}: {round(value, 4)}  ({of}; from {sources.get(key, '?')})")
    lines.append(f"caveats: {len(out.get('caveats') or [])} (see the ranking report)")
    return lines


def _uncertainty(payloads: dict[str, dict[str, Any]]) -> list[str]:
    """One line per stored result: how many steps, and items per level.

    Counts only -- the statements themselves are behind `read_uncertainty`, so
    the sheet every agent carries stays a few lines long (core/uncertainty.py).
    """
    from .uncertainty import LEVELS, TAG, capture, _tool_for

    lines = []
    for name, record in payloads.items():
        payload = record.get("payload") or {}
        steps = (payload.get("run") or {}).get("steps")
        derived = steps is None
        if derived:
            steps, block = capture(_tool_for(name), payload)
        else:
            block = payload.get("uncertainty") or {}
        counts = " ".join(f"{TAG[l]} {len(block.get(l) or [])}" for l in LEVELS)
        lines.append(f"{name}: {len(steps)} steps | {counts}"
                     + (" (derived; stored before the step log)" if derived else ""))
    return lines or ["none stored yet"]


def _figures(store: Any) -> list[str]:
    """Run figures whose files live in this session's folder."""
    from .figure_refs import RUN_INDEX, _load_runs

    base = str(store.dir.resolve()).lower()
    names = [e["placeholder"] for e in _load_runs(RUN_INDEX).values()
             if str(e.get("tif") or e.get("png") or "").lower().startswith(base)]
    return sorted(names)


def build(record: Any, store: Any) -> str:
    """The fact sheet as markdown."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    place, window = record.place, record.window
    bbox = ", ".join(f"{v:.4f}" for v in place.bbox) if place.bbox else "NOT SET"
    out = [f"# Fact sheet -- session {record.session_id}",
           f"updated: {now} | turns: {record.turns}",
           "",
           "## AOI and window",
           f"aoi: {place.display or 'NOT SET'}",
           f"bbox_wsen: {bbox}",
           f"window: {window.line() or 'NOT SET'}",
           f"confirmed_by_user: {'yes' if record.confirmed else 'no'}"]

    payloads = _payloads(store)
    out += ["", "## Stored results"]
    for name, label, workflow, output in STEPS:
        if name in payloads:
            out.append(f"{label}: stored {payloads[name].get('cached', '?')} ({workflow})")
    absent = [w for n, _, w, _ in STEPS[:7] if n not in payloads]
    out.append("not stored: " + (", ".join(absent) or "none"))

    from . import dating

    zone = dating.store_zone(store) if store is not None else None
    for name, label, workflow, output in STEPS:
        if name in payloads and name in SECTIONS:
            out += ["", f"## {label}" + (f" -- {output}" if output else "")]
            try:
                out += SECTIONS[name](payloads[name]["payload"])
                # When this data was acquired, with its zone: quoted beside
                # any figure taken from it (user decision 2026-10-26).
                out += [f"dated: {t}" for t in dating.lines(name, payloads[name]["payload"], zone)]
            except Exception as exc:                           # noqa: BLE001
                out.append(f"unreadable: {type(exc).__name__}: {exc}"[:200])

    out += ["", "## Ranking inputs"] + _indices(payloads, _lulc_failure(store))
    out += ["", "## Uncertainty (steps and items: read_uncertainty)"] + _uncertainty(payloads)

    flows = list(getattr(record, "flows_run", None) or [])
    out += ["", "## Analyses run", ", ".join(flows) or "none"]
    figures = _figures(store) if store is not None else []
    if figures:
        out += ["", "## Figures from this session"] + figures
    out += ["", "Rule: use these values as given. Do not recompute, re-ask or "
                "fill a MISSING value. When you quote a value, say when its data was "
                "acquired, copying the date from the section's 'dated:' line (the date "
                "only, no time and no time zone)."]
    return "\n".join(out) + "\n"


def write(record: Any, store: Any) -> Path | None:
    """Rebuild `sessions/<id>/facts.md`. Never raises: a fact sheet that
    cannot be written costs the next turn its context, not this turn its run."""
    if record is None or store is None:
        return None
    try:
        store.dir.mkdir(parents=True, exist_ok=True)
        path = store.dir / FILE
        path.write_text(build(record, store), encoding="utf-8")
        return path
    except Exception:                                          # noqa: BLE001
        return None


def read(record: Any) -> str:
    """The fact sheet on disk for this session, or ""."""
    if record is None or not getattr(record, "session_id", ""):
        return ""
    from . import session

    path = session.folder(record.session_id) / FILE
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
