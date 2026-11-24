"""The blocks of Modules 1-10: the paragraphs and tables their vault templates
place (`{{fact:<block>}}`), each built from the stored payloads.

The templates are LIVE vault notes, "Module N - <title> template", read by
`core/report_render.py` exactly as Modules 11 and 12 are. The note holds the
headings, the order and every fixed sentence; a block holds what is built
from the data -- a table, a sentence carrying values, a section whose heading
depends on what is stored. Change the layout in the vault, the content here.

Rules the blocks keep (they are the old renderers' rules, CP5):

* **Drawn from the stored payload, never recomputed**, and
  never fetched. A block whose input is not stored is NOT KNOWN and says which
  tool would produce it; a block that has nothing to add is empty.
* **Every sentence is built from the payload** (D-32).
* **Each block registers the payloads it rests on** (`Ctx.rest`), so the
  module's "When the data was acquired" and "How sure are we?" (D-34) are
  built from what the module showed, in the order it showed it.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import dating, report_config
from .outputs import (ALIASES, NOT_KNOWN, TOOL_FOR, _bridges_line, _n, _note_status, _pct,
                      _table, _window, best_ranked, depth_band_rows, depth_for,
                      module_product)
from .report_render import Ctx, Fact, nk, stale_population
from .workflows.osm_context import category_label

# The modules a template may be rendered for, by slot (registry v2).
MODULES = ("data_availability", "weather", "land_use_pre_event", "terrain",
           "water_and_structures", "mcdm_pre_ranking", "mcdm_ranking", "sensitivity",
           "essential_services_hev", "human_health_risks_hev")


# -- building blocks ------------------------------------------------------------------
def block(key: str, lines: list[str] | str, source: str) -> Fact:
    """A block of text; an empty one renders as nothing."""
    text = (lines if isinstance(lines, str) else "\n".join(lines)).strip("\n")
    return Fact(key, text=text, source=source) if text.strip() else Fact(key, "", source=source)


def empty(key: str) -> Fact:
    return Fact(key, "", source="nothing to add")


def missing(key: str, role: str) -> Fact:
    tool = TOOL_FOR.get(role, role)
    return nk(key, f"no stored `{ALIASES[role][0]}` (run {tool}).", tool)


def stated(key: str, text: str, reason: str) -> Fact:
    """Not known, in the block's own words (rendered as written)."""
    f = nk(key, reason)
    f.text = text
    return f


def zone(c: Ctx) -> str | None:
    if not hasattr(c, "_zone"):
        c._zone = dating.store_zone(c.store)
    return c._zone


# -- Module 1: data availability ----------------------------------------------------
def b_availability_window_check(c: Ctx) -> Fact:
    name, p = c.inp.get_named("availability")
    if p is None:
        return empty("availability_window_check")
    period = p.get("period") or {}
    window = _window(c.store)
    if window and (period.get("start"), period.get("end")) != tuple(window):
        return block("availability_window_check",
                     f"> **This check covers {period.get('start')} to {period.get('end')}; "
                     f"the experiment's window is {window[0]} to {window[1]}.** It was "
                     "fetched for another window and must be re-fetched before it is "
                     "relied on.", f"`{name}` period and event.md window")
    return empty("availability_window_check")


def b_availability_clock_change(c: Ctx) -> Fact:
    _, p = c.inp.get_named("availability")
    change = ((p or {}).get("period") or {}).get("clock_change")
    if not change:
        return empty("availability_clock_change")
    return block("availability_clock_change", f"> {change[0].upper()}{change[1:]}.",
                 "data_availability period.clock_change")


def b_availability_searched(c: Ctx) -> Fact:
    name, p = c.inp.get_named("availability")
    if p is None:
        return missing("availability_searched", "availability")
    c.rest(name, p)
    caveat = (p.get("cloud") or {}).get("caveat")
    if caveat:
        c.sure_extra.append(f"{caveat}")
    period = p.get("period") or {}
    return block("availability_searched",
                 f"Searched {period.get('start')} to {period.get('end')} in local days "
                 f"({period.get('timezone') or 'UTC'}, UTC{period.get('utc_offset_h', 0):+g} h "
                 "on the first day), "
                 f"retrieved {p.get('retrieved_at', '--')}. An instance is "
                 f"{p.get('instance_definition', 'one acquisition')}.", f"`{name}`")


def b_availability_table(c: Ctx) -> Fact:
    name, p = c.inp.get_named("availability")
    if p is None:
        return empty("availability_table")
    days = (p.get("period") or {}).get("days") or []
    rows = []
    for prod in p.get("products") or []:
        cells = []
        for d in days:
            rec = (prod.get("days") or {}).get(d) or {}
            st = rec.get("status", "--")
            cells.append(str(rec.get("count", 0)) if st == "available"
                         else {"none": "0", "not_checkable": "no data", "in_gibs": "G",
                               "not_produced": "--",
                               "unreachable": "?"}.get(st, st))
        rows.append([prod.get("label", prod.get("id")), *cells])
    return block("availability_table", _table(["Product", *[d[5:] for d in days]], rows),
                 f"`{name}` products[].days")


def b_availability_not_checkable(c: Ctx) -> Fact:
    _, p = c.inp.get_named("availability")
    if p is None:
        return empty("availability_not_checkable")
    days = (p.get("period") or {}).get("days") or []
    nc = [prod for prod in p.get("products") or []
          if (prod.get("totals") or {}).get("days_not_checkable")]
    if not nc:
        return empty("availability_not_checkable")
    out = ["Where a day has no data in any archive, the model cloud cover at the sensor's "
           "overpass is all that is known:"]
    for prod in nc:
        clouds = [f"{d[5:]} " + "/".join(f"{k} {v:.0f}%" for k, v in
                                           ((prod['days'].get(d) or {}).get('cloud_pct') or {}).items())
                  for d in days if (prod["days"].get(d) or {}).get("status") == "not_checkable"]
        out.append(f"- {prod.get('label')}: " + "; ".join(clouds))
    return block("availability_not_checkable", out, "data_availability overpass cloud")


def _figure_line(key: str, payload: dict | None, source: str) -> Fact:
    fig = (payload or {}).get("figure") or {}
    if not fig.get("name"):
        return empty(key)
    return block(key, f"Figure: `{fig.get('name')}` -- {fig.get('caption', '')}", source)


def b_availability_figure(c: Ctx) -> Fact:
    """The tool's own chart -- passes per day with the overpass cloud -- as an
    image in the module (user, 2026-11-19). It used to print only the chart's
    name, so Module 1 showed the bare count grid instead."""
    from pathlib import Path

    _, p = c.inp.get_named("availability")
    fig = (p or {}).get("figure") or {}
    path = fig.get("path", "")
    if not path or not Path(path).is_file():
        return empty("availability_figure")
    c.figures.append({"name": "data_availability", "png": path,
                      "caption": fig.get("caption", "")})
    return Fact("availability_figure", path, source="data_availability figure")


# -- Module 2: weather ----------------------------------------------------------------
def b_weather_report(c: Ctx) -> Fact:
    from .reports import weather_markdown

    p = c.get("weather")
    if p is None:
        return missing("weather_report", "weather")
    c.rest("weather_grid", p)
    return block("weather_report", weather_markdown(p), "core/reports.py:weather_markdown")


# -- Module 3: land use before the event ----------------------------------------------
def b_land_cover_sentinel2(c: Ctx) -> Fact:
    lulc = c.get("lulc")
    if lulc is None:
        return empty("land_cover_sentinel2")
    scene = lulc.get("scene") or {}
    ndvi = (lulc.get("indices") or {}).get("ndvi") or {}
    when = dating.stamp(scene["datetime"], zone(c)) if scene.get("datetime") else "--"
    out = ["## Land cover: Sentinel-2", "",
           f"Scene dated {when}, "
           f"{_n(scene.get('days_before_start'), 0)} days before the window; "
           f"{_pct(scene.get('aoi_valid_fraction'))} of the land cloud-free; "
           f"{'a mosaic' if scene.get('is_mosaic') else 'one scene'}"
           f"{' (one datatake)' if scene.get('single_datatake') else ''}. "
           f"Land {_n((lulc.get('aoi') or {}).get('land_area_km2'), 1, 'km2')}.", ""]
    classes = (ndvi.get("classes") or {}).get("fractions") or {}
    out += _table(["NDVI class", "Share of observed land"],
                  [[k.replace("_", " "), _pct(v)] for k, v in classes.items()])
    stats = [[name.upper(), _n((i.get("stats") or {}).get("mean"), 3),
              _n((i.get("stats") or {}).get("p10"), 3), _n((i.get("stats") or {}).get("p90"), 3)]
             for name, i in (lulc.get("indices") or {}).items()]
    out += ["", *_table(["Index", "Mean", "p10", "p90"], stats)]
    c.rest("lulc", lulc)
    return block("land_cover_sentinel2", out, "fetch_lulc_data")


def _lulc_failure(c: Ctx) -> dict | None:
    return c.store.failure("lulc") if hasattr(c.store, "failure") else None


def b_land_cover_s2_failure(c: Ctx) -> Fact:
    if c.get("lulc") is not None:
        return empty("land_cover_s2_failure")
    failure = _lulc_failure(c)
    if not failure:
        return empty("land_cover_s2_failure")
    return block("land_cover_s2_failure",
                 ["## Land cover: Sentinel-2 failed", "",
                  f"Sentinel-2 gave no usable pre-event scene: "
                  f"{(failure.get('error') or {}).get('message', '')[:400]} "
                  + {"BelowValidFraction": "The valid-fraction threshold stepped down to 0.5; "
                                           "nothing lower is tried.",
                     "NoUsableMosaic": "Tiles from a second UTM zone were dropped first; the "
                                       "rest did not cover the AOI."}.get(
                      (failure.get("error") or {}).get("exception"), "")],
                 "the recorded lulc failure")


def b_land_cover_worldcover(c: Ctx) -> Fact:
    from .workflows.mcdm import s2_failed_at_defaults

    if c.get("lulc") is not None:
        return empty("land_cover_worldcover")
    wc = c.get("worldcover")
    if wc is None:
        return missing("land_cover_worldcover", "lulc")
    ok, why = s2_failed_at_defaults(_lulc_failure(c), (wc.get("aoi") or {}).get("bbox"))
    src = wc.get("source") or {}
    out = ["## Land cover: WorldCover " + str(src.get("time_start", ""))[:4]
           + (" (fallback)" if ok else " (not usable as the fallback)"), "",
           (f"Used because {why}" if ok else
            f"Shown for context only: {why}. The ranking does not use it."),
           "", f"{_pct((wc.get('coverage') or {}).get('valid_fraction'))} of the land "
           f"classified; land {_n((wc.get('aoi') or {}).get('land_area_km2'), 1, 'km2')}. "
           f"A snapshot of {src.get('time_start', '')[:4]}, not the weeks before the event.", ""]
    acc = (wc.get("accuracy") or {}).get("per_class") or {}
    rows = [[k.replace("_", " "), _pct(v), _n(((wc.get("classes") or {}).get("area_km2") or {}).get(k), 2),
             _n((acc.get(k) or {}).get("users_pct"), 1), _n((acc.get(k) or {}).get("producers_pct"), 1)]
            for k, v in ((wc.get("classes") or {}).get("fractions") or {}).items() if v]
    out += _table(["Class", "Share of land", "km2", "User's acc. %", "Producer's acc. %"], rows)
    c.rest("worldcover", wc)
    return block("land_cover_worldcover", out, "fetch_worldcover")


def b_land_cover_brief(c: Ctx) -> Fact:
    from .brief_report import brief_markdown

    store = c.store
    # The land cover brief is what the pipeline writes at this point of the
    # graph (BUG-015); the full pre-flood brief is kept for stores written
    # before it, and for a brief asked for in chat.
    lc = store.deliverable("land_cover_brief")
    if lc and lc.get("text"):
        return block("land_cover_brief", lc["text"], "deliverable land_cover_brief")
    parsed = store.deliverable("pre_flood_aoi_brief.parsed")
    if parsed and parsed.get("text"):
        try:
            return block("land_cover_brief", brief_markdown(json.loads(parsed["text"])),
                         "deliverable pre_flood_aoi_brief.parsed")
        except (ValueError, TypeError):
            pass
    raw = store.deliverable("pre_flood_aoi_brief")
    if raw and raw.get("text"):
        return block("land_cover_brief", raw["text"], "deliverable pre_flood_aoi_brief")
    return nk("land_cover_brief", "the land cover brief has not been written "
              "(flow norwood:land_cover_brief).")


# -- Module 4: terrain ----------------------------------------------------------------
def b_terrain_summary(c: Ctx) -> Fact:
    t = c.get("terrain")
    if t is None:
        return missing("terrain_summary", "terrain")
    c.rest("terrain", t)
    return block("terrain_summary",
                 f"Copernicus DEM GLO-30 over {_n((t.get('aoi') or {}).get('land_area_km2'), 1, 'km2')} "
                 f"of land. Slope by {(t.get('slope') or {}).get('method', '--')}.", "fetch_terrain_data")


def b_elevation_table(c: Ctx) -> Fact:
    t = c.get("terrain")
    if t is None:
        return empty("elevation_table")
    elev = (t.get("elevation") or {}).get("stats") or {}
    return block("elevation_table", _table(["Elevation (m)", "Value"],
                                           [[k, _n(elev.get(k), 1)] for k in
                                            ("min", "p10", "mean", "median", "p90", "max")]),
                 "fetch_terrain_data elevation.stats")


def b_slope_class_table(c: Ctx) -> Fact:
    t = c.get("terrain")
    if t is None:
        return empty("slope_class_table")
    classes = (t.get("slope") or {}).get("classes") or {}
    return block("slope_class_table",
                 _table(["Slope class", "Share of land", "km2"],
                        [[k.replace("_", " "), _pct(v), _n((classes.get("area_km2") or {}).get(k), 2)]
                         for k, v in (classes.get("fractions") or {}).items()]),
                 "fetch_terrain_data slope.classes")


def b_slope_class_figure(c: Ctx) -> Fact:
    t = c.get("terrain")
    path = ((((t or {}).get("slope") or {}).get("classes") or {}).get("raster") or {}).get("path")
    return block("slope_class_figure", "Figure: the slope class raster (`slope_class`)." if path else "",
                 "fetch_terrain_data slope.classes.raster")


# -- Module 5: permanent water and structures ------------------------------------------
def b_water_classes_table(c: Ctx) -> Fact:
    w = c.get("water")
    if w is None:
        return missing("water_classes_table", "water")
    c.rest("permanent_water", w)
    water = w.get("water") or {}
    return block("water_classes_table",
                 _table(["Class", "km2", "Share of land"],
                        [[k.replace("_", " "), _n((water.get(k) or {}).get("area_km2"), 3),
                          _pct((water.get(k) or {}).get("land_fraction"), 2)]
                         for k in ("permanent", "seasonal", "ephemeral", "never_water", "unobserved")]),
                 "fetch_permanent_water")


def b_water_threshold_note(c: Ctx) -> Fact:
    w = c.get("water")
    if w is None:
        return empty("water_threshold_note")
    return block("water_threshold_note",
                 f"JRC Global Surface Water; permanent at >= "
                 f"{(w.get('thresholds') or {}).get('permanent')}% occurrence. Shares are of the land.",
                 "fetch_permanent_water thresholds")


def _where(c: Ctx, key: str) -> tuple[dict | None, Fact | None]:
    where = c.get("where")
    if where is None:
        return None, missing(key, "where")
    c.rest("where_am_i", where)
    return where, None


def b_aoi_admin_places(c: Ctx) -> Fact:
    where, gap = _where(c, "aoi_admin_places")
    if gap:
        return gap
    admin = where.get("admin") or {}
    return block("aoi_admin_places",
                 f"{', '.join((admin.get('second_order') or {}).get('units') or []) or '--'} "
                 f"({', '.join((admin.get('first_order') or {}).get('units') or []) or '--'}), "
                 f"{admin.get('country', '--')}. Cities and towns inside: "
                 f"{', '.join(p['name'] for p in admin.get('major_places') or []) or 'none mapped'}.",
                 "where_am_i admin")


def b_osm_counts_table(c: Ctx) -> Fact:
    counts = (((c.get("where") or {}).get("counts") or {}).get("counts")) or {}
    if not counts:
        return empty("osm_counts_table")
    return block("osm_counts_table", _table(["Mapped in OpenStreetMap", "Count"],
                                            [[k.replace("_", " "), _n(v)] for k, v in counts.items()]),
                 "where_am_i counts")


def b_osm_counts_missing(c: Ctx) -> Fact:
    gone = (((c.get("where") or {}).get("counts") or {}).get("missing")) or []
    return block("osm_counts_missing",
                 f"Not measured (the request failed): {', '.join(gone)}." if gone else "",
                 "where_am_i counts.missing")


def b_buildings_by_category(c: Ctx) -> Fact:
    where, gap = _where(c, "buildings_by_category")
    if gap:
        return gap
    fac = (where.get("facilities") or {}).get("categories")
    if not fac:
        return nk("buildings_by_category", "buildings of interest were not located: this "
                  "`where_am_i` was stored before facilities were located (2026-10-25); "
                  "re-run it.")
    out = []
    for key, cat in fac.items():
        names = [i["name"] for i in cat.get("items") or [] if i.get("name")]
        out.append(f"- **{category_label(key)}**: {cat.get('count')} mapped"
                   + (f" -- {', '.join(names[:8])}" + (f" and {len(names) - 8} more" if len(names) > 8 else "")
                      if names else "")
                   + (" (list cut)" if cat.get("truncated") else ""))
    return block("buildings_by_category", out, "where_am_i facilities")


def b_water_influences_section(c: Ctx) -> Fact:
    """With its heading: the section is shown only when categories exist (the
    old renderer's rule, kept by the move to the vault)."""
    where, gap = _where(c, "water_influences_section")
    if gap:
        return gap
    infl = (where.get("influences") or {}).get("categories") or {}
    if not infl:
        return empty("water_influences_section")
    return block("water_influences_section",
                 ["## Water infrastructure and influences", "",
                  *_table(["Category", "Mapped", "Inside the AOI", "Searched"],
                          [[cat.get("label", k), _n(cat.get("count")), _n(cat.get("inside_aoi")),
                            cat.get("searched", "--")] for k, cat in infl.items()])],
                 "where_am_i influences")


def b_where_figure(c: Ctx) -> Fact:
    return _figure_line("where_figure", c.get("where"), "where_am_i figure")


# -- Modules 6-8: the decision ---------------------------------------------------------
def b_mcdm_decision_matrix(c: Ctx) -> Fact:
    """Module 6: from its own step (mcdm_decision_matrix, 2026-11-19), else
    from a ranking run, which carries the same matrix and weights."""
    from .mcdm_report import decision_matrix_markdown

    name, p = c.inp.get_named("matrix")
    if p is None:
        name, p = c.inp.get_named("ranking")
    if p is None:
        return missing("mcdm_decision_matrix", "matrix")
    c.rest(name, p)
    c.sure_extra.extend(p.get("index_caveats") or [])
    return block("mcdm_decision_matrix", decision_matrix_markdown(p),
                 "core/mcdm_report.py:decision_matrix_markdown")


def b_mcdm_matrix_figure(c: Ctx) -> Fact:
    """Module 6 as one picture (user, 2026-11-22): benefit and cost criteria
    apart, cells coloured by how each product compares on its row, each
    weight beside its equal start. The one the tool drew; for a matrix
    stored before it drew one, drawn now from the stored payload."""
    from pathlib import Path

    from . import charts

    name, p = c.inp.get_named("matrix")
    if p is None:
        name, p = c.inp.get_named("ranking")
    if p is None:
        return nk("mcdm_matrix_figure", "the decision matrix is not stored",
                  "mcdm_decision_matrix")
    path = ((p.get("figure") or {}).get("path")) or ""
    if (not path or not Path(path).is_file()
            or Path(path).name != "mcdm_decision_matrix.png"):
        path = str(Path(c.store.dir) / "figures" / "mcdm_decision_matrix.png")
        try:
            charts.decision_matrix_figure(p, path)
        except Exception as exc:                               # noqa: BLE001
            return nk("mcdm_matrix_figure", f"the matrix figure could not be drawn "
                      f"({type(exc).__name__})", "mcdm_decision_matrix")
    c.rest(name, p)
    c.figures.append({"name": "mcdm_decision_matrix", "png": path,
                      "caption": "Scores (benefit: higher is better; cost: lower is better) "
                                 "and the weight each criterion carries for this event."})
    return Fact("mcdm_matrix_figure", path, source="mcdm_decision_matrix, core/charts.py")


def b_mcdm_ranking(c: Ctx) -> Fact:
    from .mcdm_report import ranking_markdown

    name, p = c.inp.get_named("ranking")
    if p is None:
        return missing("mcdm_ranking", "ranking")
    c.rest(name, p)
    return block("mcdm_ranking", ranking_markdown(p), "core/mcdm_report.py:ranking_markdown")


def b_mcdm_closeness_figure(c: Ctx) -> Fact:
    """Module 7: the closeness meter (user, 2026-11-19: ideal and anti-ideal made
    intuitive). The one the ranking tool drew; for a ranking stored before it
    drew one, drawn now from the stored rows -- read, never recomputed."""
    from pathlib import Path

    from . import charts

    name, p = c.inp.get_named("ranking")
    if p is None:
        return nk("mcdm_closeness_figure", "the ranking is not stored", "rank_flood_products")
    path = ((p.get("figure") or {}).get("path")) or ""
    if not path or not Path(path).is_file():
        path = str(Path(c.store.dir) / "figures" / "topsis_closeness.png")
        try:
            charts.closeness_meter(p.get("ranking") or [], path)
        except Exception as exc:                               # noqa: BLE001
            return nk("mcdm_closeness_figure", f"the closeness chart could not be drawn "
                      f"({type(exc).__name__})", "rank_flood_products")
    c.rest(name, p)
    c.figures.append({"name": "topsis_closeness", "png": path,
                      "caption": "TOPSIS closeness between the anti-ideal (0) and the ideal (1)."})
    return Fact("mcdm_closeness_figure", path, source="rank_flood_products, core/charts.py")


def b_sensitivity_sweep(c: Ctx) -> Fact:
    from .mcdm_report import sensitivity_markdown

    name, p = c.inp.get_named("sensitivity")
    if p is None:
        return missing("sensitivity_sweep", "sensitivity")
    c.rest(name, p)
    return block("sensitivity_sweep", sensitivity_markdown(p),
                 "core/mcdm_report.py:sensitivity_markdown")


def b_sensitivity_figure(c: Ctx) -> Fact:
    """The weight-stability chart the tool drew; for a payload stored before
    the tool drew it, drawn now from that payload (charts read, never
    recompute)."""
    from pathlib import Path

    from .charts import stability_bands
    from .report_render import _figure_path

    name, p = c.inp.get_named("sensitivity")
    if p is None:
        return missing("sensitivity_figure", "sensitivity")
    path = _figure_path(c, p, "sensitivity_stability")
    if not path:
        try:
            path = stability_bands(p, Path(c.store.dir) / "figures" / "sensitivity_stability.png",
                                   event=getattr(c.store, "slug", ""))["png"]
        except Exception as exc:                               # noqa: BLE001
            # A chart that cannot be drawn costs the module its chart, never the module.
            c.details["sensitivity_figure"] = f"{type(exc).__name__}: {exc}"
            return nk("sensitivity_figure", "the weight-stability chart could not be drawn "
                      "from the stored result", "sensitivity_analysis")
    c.figures.append({"name": "sensitivity_stability", "png": path,
                      "caption": (p.get("figure") or {}).get("caption", "Weight-stability bands")})
    return Fact("sensitivity_figure", path, source="core/charts.py:stability_bands")


# -- Modules 9-10: hazard, exposure, vulnerability -------------------------------------
def _hazard_extent(c: Ctx) -> dict | None:
    """The stored extent the HEV modules describe: None without a ranking or
    a stored extent, otherwise the stored one whatever its status."""
    top, _, _ = module_product(c.store, c.inp)
    return c.get("extent") if top is not None else None


def b_hazard_product_mismatch(c: Ctx) -> Fact:
    """Says which product the module maps when it is not the best-ranked --
    the user's choice (2026-11-18) -- or when the stored extent is another."""
    pid, label, how = module_product(c.store, c.inp)
    top, top_label = best_ranked(c.store, c.inp)
    extent = _hazard_extent(c)
    if how == "chosen" and pid != top:
        rank = next((r.get("rank") for r in (c.inp.get("ranking") or {}).get("ranking") or []
                     if r.get("id") == pid), None)
        return block("hazard_product_mismatch",
                     f"> This module maps **{label}**, as you asked"
                     + (f" (ranked {rank} of the products)" if rank else "")
                     + (f". The best-ranked product is {top_label}." if top else
                        "; the products are not ranked yet."),
                     "the user's choice and rank_flood_products")
    if extent is None or extent.get("product") == pid:
        return empty("hazard_product_mismatch")
    return block("hazard_product_mismatch",
                 f"> The stored extent is {extent.get('label')}, but this module's product "
                 f"is {label}. Every figure below is the stored product's.",
                 "rank_flood_products and the stored extent")


def b_hazard_flood(c: Ctx) -> Fact:
    top, label, how = module_product(c.store, c.inp)
    if top is None:
        return nk("hazard_flood", f"{label}; the flood extent is the best-ranked product's (D-24).")
    extent = c.get("extent")
    if extent is None:
        which = "the product you chose" if how == "chosen" else "the best-ranked product"
        return nk("hazard_flood", f"the flood extent of {which}, {label}, "
                  "is not stored (run get_flood_map).", "get_flood_map")
    c.rest("get_flood_map", extent)
    if extent.get("status") == "no_flood_mapped":
        return block("hazard_flood",
                     f"{extent.get('label')} maps **no flood** over the AOI in "
                     f"{' to '.join(extent.get('window') or [])}, observing "
                     f"{_pct(extent.get('coverage'))} of the land. Every flood-dependent "
                     "figure below is NOT KNOWN for that reason -- a finding, not a "
                     "failure (D-24).", "get_flood_map")
    if extent.get("status") == "not_known":
        return nk("hazard_flood", f"{extent.get('reason')}")
    return block("hazard_flood",
                 f"{extent.get('label')}: **{_n(extent.get('flood_km2'), 2, 'km2')} flooded** "
                 f"of {_n(extent.get('observed_km2'), 1, 'km2')} observed land "
                 f"({_pct(extent.get('coverage'))} of the land observed), over "
                 f"{' to '.join(extent.get('window') or [])}. Permanent water and the "
                 "sea are excluded.", "get_flood_map")


def _depth(c: Ctx) -> tuple[dict | None, str]:
    return depth_for(c.inp, _hazard_extent(c))


def b_depth_summary(c: Ctx) -> Fact:
    dep, why = _depth(c)
    if dep is None:
        return nk("depth_summary", f"{why}.", "calc_flood_depth" if "calc_flood_depth" in why else "")
    c.rest("calc_flood_depth", dep)
    d = dep.get("depth") or {}
    bias = (dep.get("bias") or {}).get("label", "")
    return block("depth_summary",
                 f"FwDET v2.1 on the GLO-30 surface model: median depth **{_n(d.get('median_m'), 2, 'm')}**"
                 + (f" (**{bias}**)" if bias else "")
                 + f", 95th percentile {_n(d.get('p95_m'), 2, 'm')}, deepest {_n(d.get('max_m'), 2, 'm')}, over "
                 f"{_n((dep.get('flooded') or {}).get('km2_on_dem_grid'), 2, 'km2')} on the DEM grid.",
                 "calc_flood_depth")


def b_depth_band_table(c: Ctx) -> Fact:
    dep, _ = _depth(c)
    if dep is None:
        return empty("depth_band_table")
    return block("depth_band_table",
                 _table(["Band", "Depth", "km2", "Share of the flood", "Band stable"], depth_band_rows(dep)),
                 "calc_flood_depth vehicle_classes, advisory_10cm, person_bands")


def b_depth_how_firm(c: Ctx) -> Fact:
    dep, _ = _depth(c)
    if dep is None:
        return empty("depth_how_firm")
    e = dep.get("error") or {}
    return block("depth_how_firm",
                 f"How firm: across {e.get('realisations')} DEM perturbations x 3 boundaries the median "
                 f"per-pixel spread is {_n(e.get('median_sd_m'), 2, 'm')} (SD), and "
                 f"{_pct(e.get('unstable_share_vehicle'))} of the flood may change vehicle class. A "
                 "precision, not an accuracy: no observed depth exists for the AOI.",
                 "calc_flood_depth error")


def b_depth_bias(c: Ctx) -> Fact:
    dep, _ = _depth(c)
    if dep is None:
        return empty("depth_bias")
    b = dep.get("boundary") or {}
    bias = (dep.get("bias") or {}).get("label", "")
    return block("depth_bias",
                 f"**Every depth here is {bias or 'biased high'}**: the DEM includes trees and buildings, "
                 "and where they line the flood they lift the water surface. The dry edge stands "
                 f"{_n(b.get('edge_above_flood_ground_m'), 2, 'm')} above the flooded ground (medians); how "
                 "much of that is canopy or roof is not measured, so the bias is a direction, not a size. "
                 "A band or road class read from these depths may be one class too deep.",
                 "calc_flood_depth bias, boundary (BUG-014)")


# Each HEV section: the facilities it tests against the extent, and the flood
# analyst agent's flows whose deliverable is its evidence.
SECTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "critical_infrastructure": (("electric_substations", "government", "industrial_sites", "schools"),
                                ("critical_infrastructure_and_waterlogging",
                                 "power_outage_and_disruption_risk")),
    "roads_and_accessibility": (("hospitals",), ("rockfall_treefall_and_landslide_risk",)),
    "food": (("food_grocery_shops", "marketplaces"), ("essential_services",)),
    "water": (("water_facilities", "wastewater_facilities"), ("drinking_water_scarcity",)),
    "health": (("hospitals", "water_facilities", "wastewater_facilities"),
               ("human_health_risk", "electric_shock_hazard")),
}


def _exposure(section: str) -> Callable[[Ctx], Fact]:
    key = f"exposure_{section}"

    def f(c: Ctx) -> Fact:
        from .workflows.impact import facilities_in_extent

        where = c.get("where")
        if where is None or not (where.get("facilities") or {}).get("categories"):
            return nk(key, "the buildings of interest are not located (re-run where_am_i).",
                      "where_am_i")
        extent = _hazard_extent(c)
        if not extent or extent.get("status") != "flood_mapped" or not (extent.get("raster") or {}).get("path"):
            return nk(key, "no mapped flood extent to test them against.")
        hits = facilities_in_extent(where, extent["raster"]["path"], SECTIONS[section][0])
        rows = [[category_label(k), v["flooded"], v["dry"], v["not_observed"],
                 ", ".join(v["flooded_names"][:6]) or "--"] for k, v in hits.items()]
        return block(key, _table(["Mapped facility", "In the flood", "Dry", "Not observed",
                                  "Flooded, by name"], rows), "where_am_i facilities x the extent")

    return f


def _evidence(section: str) -> Callable[[Ctx], Fact]:
    key = f"evidence_{section}"

    def f(c: Ctx) -> Fact:
        flows = SECTIONS[section][1]
        ok, detail = _note_status(section)
        if not ok:
            return stated(key, f"{detail[0].upper()}{detail[1:]}. Nothing is written in its place.",
                          detail)
        for name in flows:
            d = c.store.deliverable(name)
            if d and d.get("text"):
                return block(key, [f"From the flood analyst agent's `{name}` flow, reading "
                                   f"\"{detail}\":", "", d["text"]], f"deliverable {name}")
        return stated(key, f"The note \"{detail}\" is in the vault; {NOT_KNOWN} the flood analyst "
                           f"agent's flow ({', '.join(flows)}) has not run.",
                      f"the flood analyst agent's flow ({', '.join(flows)}) has not run")

    return f


def b_roads_summary(c: Ctx) -> Fact:
    r = c.get("roads")
    if r is None:
        return missing("roads_summary", "roads")
    c.rest("assess_road_access", r)
    total = r.get("roads_km_total") or {}
    return block("roads_summary",
                 f"{_n(total.get('flooded_km'), 1, 'km')} of mapped road in the flood extent, "
                 f"{_n(total.get('dry_km'), 1, 'km')} dry, {_n(total.get('not_observed_km'), 1, 'km')} "
                 f"not observed. State: {r.get('road_state')}.", "assess_road_access")


def b_roads_by_class_table(c: Ctx) -> Fact:
    r = c.get("roads")
    if r is None:
        return empty("roads_by_class_table")
    return block("roads_by_class_table",
                 _table(["Road class", "Flooded km", "Dry km", "Not observed km"],
                        [[k, _n(v["flooded_km"], 2), _n(v["dry_km"], 2), _n(v["not_observed_km"], 2)]
                         for k, v in sorted((r.get("roads_km_by_class") or {}).items(),
                                            key=lambda kv: -kv[1]["flooded_km"])]),
                 "assess_road_access roads_km_by_class")


def b_roads_passability_km(c: Ctx) -> Fact:
    r = c.get("roads")
    km = (r or {}).get("passability_km")
    if not km:
        return empty("roads_passability_km")
    return block("roads_passability_km",
                 f"By depth (Kramer et al. 2016; depth biased high, so a road may be one class "
                 f"more passable than shown): {', '.join(f'{k} {_n(v, 2)} km' for k, v in km.items())}"
                 + (f"; {_n(r.get('passability_unstable_km'), 2)} km may change class across the "
                    "depth error realisations." if r.get("passability_unstable_km") is not None else "."),
                 "assess_road_access passability_km")


def b_roads_bridges(c: Ctx) -> Fact:
    r = c.get("roads")
    if r is None:
        return empty("roads_bridges")
    return block("roads_bridges", _bridges_line(r.get("bridges_in_flood_extent") or []),
                 "assess_road_access bridges_in_flood_extent")


def b_roads_hospital_access(c: Ctx) -> Fact:
    r = c.get("roads")
    if r is None:
        return empty("roads_hospital_access")
    return block("roads_hospital_access",
                 f"Hospitals with a flooded road within the assumed distance: "
                 f"{r.get('hospitals_access_at_risk')} of {len(r.get('hospitals') or [])}.",
                 "assess_road_access hospitals")


def b_slope_tree_notes(c: Ctx) -> Fact:
    st = c.get("slope_tree")
    if st is None:
        return empty("slope_tree_notes")
    c.rest("check_slope_tree_roads", st)
    return block("slope_tree_notes",
                 [f"Slope-and-tree notes (not a forecast): {st.get('finding')}.",
                  *[f"- {n['road']} ({n['class']}), {n['length_m']} m near "
                    f"{n['lat']:.5f}, {n['lon']:.5f}" for n in (st.get("notes") or [])[:8]]],
                 "check_slope_tree_roads")


def b_people_in_aoi(c: Ctx) -> Fact:
    pop = c.get("population")
    if pop is None:
        return missing("people_in_aoi_statement", "population")
    c.rest("population", pop)
    if stale_population(pop):
        return stated("people_in_aoi_statement",
                      f"{NOT_KNOWN} the stored population came from the retired census tool; "
                      "it is not re-read. Run get_pop_data for a WorldPop figure.",
                      "the stored population came from the retired census tool")
    lo, hi = (pop.get("range") or [None, None])[:2]
    return block("people_in_aoi_statement",
                 f"{_n(pop.get('population'))} people live in the AOI "
                 f"(range {_n(lo)} to {_n(hi)}; {pop.get('vintage', '')}, "
                 f"{pop.get('method', '')}). How many were in the flood is not computed.",
                 "get_pop_data")


def b_rests_on_where_am_i(c: Ctx) -> Fact:
    """Renders nothing: declares that the module rests on the OpenStreetMap
    context, so it is dated and qualified in When and How sure. A template
    places it where the module uses that context."""
    where = c.get("where")
    if where is not None:
        c.rest("where_am_i", where)
    return empty("rests_on_where_am_i")


def b_climate_zone(c: Ctx) -> Fact:
    zone_name, basis = report_config.climate_zone(getattr(c.store, "slug", ""))
    if not zone_name:
        return nk("climate_zone_statement", "no climate zone is set for this experiment "
                  "(core/report_config.py), so no climate-specific risk is listed.")
    c.sure_extra.append(f"Climate zone: {basis}.")
    return block("climate_zone_statement", f"{zone_name} -- {basis}.", "core/report_config.py")


# -- Modules 9 and 10: hazard, exposure, vulnerability (user decision 2026-11-02) ------
def _hazard_map(c: Ctx) -> tuple[dict | None, str]:
    """The stored hazard-and-exposure map, when it was drawn for the extent
    the module describes; otherwise None and why. A product that mapped no
    flood still has its map: what it saw and did not see."""
    ext, why = c.observed()
    if not ext:
        return None, why
    hm = c.get("hazard_map")
    if hm is None:
        return None, "the hazard-and-exposure map is not drawn yet (run map_hazard_exposure)"
    if (hm.get("flood") or {}).get("raster") != (ext.get("raster") or {}).get("path"):
        return None, (f"the stored hazard-and-exposure map was drawn for another flood extent, "
                      f"not {ext.get('label')}'s (re-run map_hazard_exposure)")
    c.rest("map_hazard_exposure", hm)
    return hm, ""


def _hev_map(fig_name: str) -> Callable[[Ctx], Fact]:
    key = f"hev_map_{fig_name}"

    def f(c: Ctx) -> Fact:
        from pathlib import Path

        hm, why = _hazard_map(c)
        if hm is None:
            return nk(key, why, "map_hazard_exposure")
        png = ((hm.get("maps") or {}).get(fig_name) or {}).get("png", "")
        if not png or not Path(png).is_file():
            return nk(key, f"the {fig_name.replace('_', ' ')} map was not drawn", "map_hazard_exposure")
        ext, _ = c.observed()
        layers = ", ".join(hm["maps"][fig_name]["layers"])
        caption = (f"{hm['flood']['label']} flood extent (deep red; grey = not observed) with {layers}"
                   if (ext or {}).get("status") == "flood_mapped" else
                   f"{hm['flood']['label']} mapped no flood; land it did not observe is "
                   f"shaded. With {layers}")
        c.figures.append({"name": f"hazard_exposure_{fig_name}", "png": png,
                          "caption": caption})
        return Fact(key, png, source="map_hazard_exposure")

    return f


def _hev_exposure_table(fig_name: str) -> Callable[[Ctx], Fact]:
    key = f"hev_exposure_{fig_name}"

    def f(c: Ctx) -> Fact:
        hm, why = _hazard_map(c)
        if hm is None:
            return nk(key, why, "map_hazard_exposure")
        layers = ((hm.get("maps") or {}).get(fig_name) or {}).get("layers") or []
        rows = []
        for name in layers:
            e = (hm.get("exposure") or {}).get(name) or {}
            h = (e.get("in_hazard") or {}).get("flood_extent") or {}
            rows.append([e.get("label", name), _n(e.get("located")), _n(h.get("in")),
                         _n(h.get("out")), _n(h.get("not_observed")),
                         ", ".join(h.get("names_in")[:5]) if h.get("names_in") else "--"])
            if e.get("cap_reached"):
                c.sure_extra.append(f"{e.get('label')}: more are mapped than were located; "
                                    "the counts are of the located ones.")
        return block(key, _table(["Mapped facility", "Located", "In the flood", "Outside it",
                                  "Not observed", "In the flood, by name"], rows),
                     "map_hazard_exposure exposure x the flood extent")

    return f


def b_hev_food_clusters(c: Ctx) -> Fact:
    hm, why = _hazard_map(c)
    if hm is None:
        return nk("hev_food_clusters", why, "map_hazard_exposure")
    cl = hm.get("food_clusters") or []
    if not cl:
        return block("hev_food_clusters", "No food shop or market is mapped in the AOI.",
                     "map_hazard_exposure food_clusters")
    hit = [x for x in cl if ((x.get("in_hazard") or {}).get("flood_extent") or {}).get("in")]
    outlets = sum(x["count"] for x in cl)
    flooded = sum(x["in_hazard"]["flood_extent"]["in"] for x in hit)
    unseen = sum(((x.get("in_hazard") or {}).get("flood_extent") or {}).get("not_observed") or 0
                 for x in cl)
    biggest = max(cl, key=lambda x: x["count"])
    ext, _ = c.observed()
    if (ext or {}).get("status") == "no_flood_mapped":
        # No flood mapped is not "none in the flood": unobserved outlets are unknown.
        where = (f"None is in a mapped flood; {_n(unseen)} are on land the product did not "
                 "observe, so whether they were flooded is not known. **Not observed does "
                 "not mean not affected.**" if unseen else
                 "None is in a mapped flood, and every one is on observed land.")
    else:
        where = f"{_n(flooded)} outlets in {_n(len(hit))} clusters are in the flood."
    return block("hev_food_clusters",
                 f"{_n(outlets)} food shops and markets in {_n(len(cl))} clusters (each at most "
                 f"{_n(hm.get('cluster_rule', {}).get('max_diameter_km'), 0)} km across; the "
                 f"largest holds {_n(biggest['count'])}). {where}",
                 "map_hazard_exposure food_clusters")


def b_hev_roads_bridges(c: Ctx) -> Fact:
    """Module 9's roads and bridges (user, 2026-11-22): the through routes
    (motorway to secondary) the flood reached or the product did not see, by
    name; the bridges in the flood; hospitals with a flooded road nearby.
    The connectivity facts the agents read for network disruption."""
    from .workflows.impact import HOSPITAL_DISTANCE_M

    key = "hev_roads_bridges"
    r = c.get("roads")
    if r is None:
        return missing(key, "roads")
    ext, _ = c.observed()
    want = ((ext or {}).get("raster") or {}).get("path")
    if want and r.get("extent_raster") and r["extent_raster"] != want:
        return nk(key, "the stored road check is for another flood extent; re-run Module 9.",
                  "assess_road_access")
    if "main_roads" not in r:
        return nk(key, "the stored road check predates the main-road list; re-run Module 9.",
                  "assess_road_access")
    c.rest("assess_road_access", r)
    rows = [m for m in r["main_roads"] if m["flooded_km"] > 0 or m["not_observed_km"] > 0]
    flooded = sum(m["flooded_km"] for m in rows)
    unseen = sum(m["not_observed_km"] for m in rows)
    lines = []
    if rows:
        lines += _table(["Main road", "Class", "In the flood, km", "Not observed, km"],
                        [[m["road"], m["class"].replace("_", " "), _n(m["flooded_km"], 2),
                          _n(m["not_observed_km"], 2)] for m in rows[:12]])
        if len(rows) > 12:
            lines.append(f"_and {len(rows) - 12} more main roads._")
        lines.append("")
    lines.append(f"Main roads (motorway to secondary): {_n(flooded, 1, 'km')} in the flood, "
                 f"{_n(unseen, 1, 'km')} on ground the product did not observe -- state not "
                 "known, never open." if rows else
                 "No main road (motorway to secondary) is in the flood or on unobserved ground.")
    lines.append(_bridges_line(r.get("bridges_in_flood_extent") or []))
    hosp = r.get("hospitals") or []
    if hosp:
        lines.append(f"Hospitals with a flooded road within {_n(HOSPITAL_DISTANCE_M, 0, 'm')} "
                     f"(an assumed distance, not a route): {_n(r.get('hospitals_access_at_risk'))} "
                     f"of {_n(len(hosp))}.")
    lines.append("Roads are those OpenStreetMap maps; whether a road is passable needs its "
                 "depth, which is not computed.")
    return block(key, lines, "assess_road_access main_roads, bridges, hospitals")


def b_hev_people(c: Ctx) -> Fact:
    """Two numbers (user decision 2026-11-02): the people in the AOI and the
    population affected -- the people inside the mapped flood extent."""
    from .report_render import f_people_aoi, f_people_extent

    pop = c.get("population")
    aoi, inside = f_people_aoi(c), f_people_extent(c)
    if pop is not None:
        c.rest("population", pop)
    if not aoi.known:
        return nk("hev_people", aoi.reason, "get_pop_data")
    lines = [f"**{_n(aoi.value)} people** live in the AOI ({pop.get('vintage', '')})."]
    ext, _ = c.observed()
    counted = (pop or {}).get("flood_extent") or {}
    if ((ext or {}).get("status") == "no_flood_mapped"
            and counted.get("extent_raster") == (ext.get("raster") or {}).get("path")):
        # Never "0 people affected": with nothing mapped, the people on land
        # the product did not see are unknown, not unaffected.
        unseen = counted.get("people_not_observed")
        lines.append(f"No flood was mapped on the land {ext.get('label')} observed. "
                     + (f"**{_n(unseen)} people** live on land it did not observe, so the "
                        "population affected is not known. **Not observed does not mean "
                        "not affected.**" if unseen else
                        "Every person in the AOI lives on land it observed."))
        return block("hev_people", " ".join(lines), "get_pop_data (WorldPop)")
    if inside.known:
        unseen = ((pop or {}).get("flood_extent") or {}).get("people_not_observed")
        lines.append(f"**Population affected: {_n(inside.value)} people** live inside the mapped "
                     "flood extent." + (f" A further {_n(unseen)} live on land the product did "
                                        "not observe. **Not observed does not mean not "
                                        "affected.**" if unseen else ""))
    else:
        lines.append(f"Population affected: {NOT_KNOWN} {inside.reason}.")
    return block("hev_people", " ".join(lines), "get_pop_data (WorldPop)")


INFORM_SRC = "fetch_inform_risk (INFORM, EU JRC)"


def b_hev_income_group(c: Ctx) -> Fact:
    """One phrase at the head of Vulnerability: the World Bank income group."""
    inf = c.get("inform")
    if inf is None:
        return nk("hev_income_group", "the INFORM scores are not stored", "fetch_inform_risk")
    ig = inf.get("income_group") or {}
    if not ig.get("value"):
        # A payload from before 2026-11-11 has no income group; re-run the tool.
        return nk("hev_income_group", "the income group is not stored with the INFORM scores",
                  "fetch_inform_risk")
    c.rest("inform_risk", inf)
    # "Upper middle income" -> "upper-middle-income"
    group = ig["value"].lower().removesuffix(" income").replace("upper middle", "upper-middle").replace(
        "lower middle", "lower-middle")
    article = "an" if group[:1] in "aeiou" else "a"
    read = dating.day(ig.get("retrieved_at"), dating.store_zone(c.store)) if ig.get("retrieved_at") else ""
    return block("hev_income_group",
                 f"{inf.get('country', 'The country')} is {article} **{group}-income** country "
                 f"(**World Bank income group**, current classification"
                 f"{', read ' + read if read else ''}).", "World Bank country record")


def b_hev_population_map(c: Ctx) -> Fact:
    """The WorldPop map get_pop_data drew (2026-11-18): people per pixel with
    the flood extent and the unobserved land on top -- for THIS extent only."""
    from pathlib import Path

    pop = c.get("population")
    if pop is None:
        return nk("hev_population_map", "the AOI population is not stored", "get_pop_data")
    png = (pop.get("figure") or {}).get("path", "")
    if not png or not Path(png).is_file():
        return nk("hev_population_map", "the population map was not drawn (re-run get_pop_data)",
                  "get_pop_data")
    ext, _ = c.observed()
    if ext and ((pop.get("flood_extent") or {}).get("extent_raster")
                != (ext.get("raster") or {}).get("path")):
        return nk("hev_population_map", "the stored population map is for another flood extent "
                  "(re-run get_pop_data)", "get_pop_data")
    c.rest("population", pop)
    c.figures.append({"name": "population_map", "png": png,
                      "caption": (pop.get("figure") or {}).get("caption", "")})
    return Fact("hev_population_map", png, source="get_pop_data (WorldPop)")


def _inform_figure(key: str, draw: str) -> Callable[[Ctx], Fact]:
    """A figure of the stored INFORM payload, drawn into the store."""

    def f(c: Ctx) -> Fact:
        from pathlib import Path

        from . import charts_inform

        inf = c.get("inform")
        if inf is None:
            return nk(key, "the INFORM scores are not stored", "fetch_inform_risk")
        if draw == "trend" and (inf.get("trend") or {}).get("status") != "ok":
            return nk(key, "the INFORM trend is not stored with the scores "
                      f"({(inf.get('trend') or {}).get('status', 'not fetched')})", "fetch_inform_risk")
        c.rest("inform_risk", inf)
        # One short line, attributed to the tool and the one release the chart
        # shows (user, 2026-11-11: every source on every chart was unreadable).
        # The full list stays in "When the data was acquired".
        zone = dating.store_zone(c.store)
        if draw == "trend":
            t = inf.get("trend") or {}
            line = (f"Source: INFORM risk tool (EC JRC) -- {t.get('series', 'INFORM')} series, "
                    f"published {dating.day(t['published'], zone) if t.get('published') else 'date not recorded'}")
        else:
            rel = inf.get("release") or {}
            line = (f"Source: INFORM risk tool (EC JRC) -- {rel.get('name', 'INFORM')}, published "
                    f"{dating.day(rel['published'], zone) if rel.get('published') else 'date not recorded'}; "
                    "climate change: INFORM Climate Change 2022")
        png = Path(c.store.dir) / "figures" / f"inform_{draw}.png"
        try:
            path = getattr(charts_inform, draw)(inf, png, line)
        except Exception as exc:                               # noqa: BLE001
            c.details[key] = f"{type(exc).__name__}: {exc}"
            return nk(key, f"the INFORM {draw} figure could not be drawn", "fetch_inform_risk")
        caption = ("National INFORM meters: overall risk, vulnerability, lack of coping capacity "
                   "and climate change risk (2050)" if draw == "meters" else
                   "INFORM trend: vulnerability, lack of coping capacity and natural hazard "
                   "(all natural hazards)")
        c.figures.append({"name": f"inform_{draw}", "png": str(path), "caption": caption})
        return Fact(key, str(path), source=INFORM_SRC)

    return f


def b_hev_inform(c: Ctx) -> Fact:
    """The meters' numbers in one line, under the figure: the text view of the
    meters, and what Chanakya reads (a figure reaches no prompt)."""
    inf = c.get("inform")
    if inf is None:
        return nk("hev_inform", "the INFORM scores are not stored", "fetch_inform_risk")
    from .charts_inform import meter_values

    c.rest("inform_risk", inf)
    rel = inf.get("release") or {}
    pub = dating.day(rel.get("published"), "UTC") if rel.get("published") else "--"
    # Each score bold, label and value together, so the line scans like the meters.
    parts = [f"**{m['title'] if m['title'].startswith('INFORM') else m['title'].lower()} "
             f"{_n(m['value'], 1) if m['value'] is not None else 'not known'}**"
             for m in meter_values(inf)]
    # National level said first and in bold (user, 2026-11-19).
    return block("hev_inform", "**These are national-level figures for the whole country, "
                 "not measurements of the area of interest.** "
                 f"Scores out of 10, **higher is worse**: {'; '.join(parts)} "
                 f"({rel.get('name', 'INFORM')}, published {pub}; climate change: INFORM Climate "
                 "Change 2022, pessimistic scenario).", INFORM_SRC)


def b_hev_inform_trend_line(c: Ctx) -> Fact:
    inf = c.get("inform")
    t = (inf or {}).get("trend") or {}
    if t.get("status") != "ok":
        return empty("hev_inform_trend_line")
    from .charts_inform import TREND_PANELS

    years = [y for y in t.get("years") or [] if y.get("status") == "ok"]
    if not years:
        return empty("hev_inform_trend_line")
    first, last = years[0], years[-1]
    parts = [f"**{title.lower()} {_n(first.get(k), 1)} to {_n(last.get(k), 1)}**"
             for k, title in TREND_PANELS]
    gaps = [str(y["year"]) for y in t.get("years") or [] if y.get("status") != "ok"]
    return block("hev_inform_trend_line",
                 f"Trend {first['year']} to {last['year']} ({t.get('series')} series): {'; '.join(parts)}"
                 + (f"; no data for {', '.join(gaps)}" if gaps else "") + ".", INFORM_SRC)


def b_hev_previous_floods(c: Ctx) -> Fact:
    """One line; the events themselves are the timeline figure below it
    (`previous_disasters_timeline`), not a list."""
    d = c.get("disasters")
    if d is None:
        return nk("hev_previous_floods", "earlier disasters are not searched yet",
                  "search_previous_disasters")
    c.rest("search_previous_disasters", d)
    events = [e for e in d.get("events") or [] if e.get("kind") in ("flood", "storm", "cyclone", "")]
    if not events:
        return block("hev_previous_floods", f"No flood is recorded in the registers searched "
                     f"before {d.get('search_cutoff')}.", "search_previous_disasters")
    e = max(events, key=lambda x: x.get("start", ""))
    km = f", {_n(e.get('km_from_aoi'), 0)} km from the AOI" if e.get("km_from_aoi") is not None else ""
    return block("hev_previous_floods",
                 f"**{len(events)} floods and storms** recorded before {d.get('search_cutoff')}; "
                 f"the latest: **{e.get('name')}, {e.get('start')}**{km} "
                 f"({e.get('cite') or e.get('source')}).", "search_previous_disasters")


# -- when and how sure (computed last: they read what the other blocks rested on) -------
LEVEL_TITLES = {"input": "Input data", "tool": "Processing", "pipeline": "This run",
                "decision": "Decisions"}


def b_data_acquired(c: Ctx) -> Fact:
    """When the data was acquired (user decision 2026-10-26)."""
    lines = [t for t in dating.section(c.rests_on, zone(c)) if t.startswith("- ")]
    if not lines:
        return nk("data_acquired", "nothing this module rests on is stored yet.")
    return block("data_acquired", lines, "core/dating.py, from the stored results' own time fields")


def b_how_sure(c: Ctx) -> Fact:
    """The four-level uncertainty from the payloads' own blocks (D-34, RQ6)."""
    out: list[str] = []
    by_level: dict[str, list[str]] = {k: [] for k in LEVEL_TITLES}
    legacy: list[str] = []
    for name, payload in c.rests_on:
        unc = payload.get("uncertainty") if isinstance(payload, dict) else None
        if not isinstance(unc, dict):
            legacy.append(name)
            continue
        for level in LEVEL_TITLES:
            for item in unc.get(level) or []:
                if isinstance(item, dict) and item.get("statement"):
                    basis = item.get("basis", "")
                    by_level[level].append(f"- {item['statement']}"
                                           + (f" _({basis})_" if basis else "")
                                           + f" -- `{name}`")
    for level, title in LEVEL_TITLES.items():
        if by_level[level]:
            out += [f"**{title}**", *by_level[level], ""]
    for line in c.sure_extra:
        out.append(f"- {line}")
    if legacy:
        out += ["", "Stored before the tool contract (2026-10-25), so these carry no "
                "uncertainty block of their own; their caveats are in the sections "
                f"above: {', '.join(f'`{n}`' for n in legacy)}."]
    if not out:
        return nk("how_sure", "nothing this module rests on is stored yet.")
    return block("how_sure", out, "the uncertainty blocks of the payloads this module rests on")


# -- the registry ------------------------------------------------------------------------
# In the order the modules show them: a block registers what it rests on as it
# is computed, so this order is the order of "When" and "How sure".
BLOCKS: dict[str, Callable[[Ctx], Fact]] = {
    "availability_window_check": b_availability_window_check,
    "availability_clock_change": b_availability_clock_change,
    "availability_searched": b_availability_searched,
    "availability_table": b_availability_table,
    "availability_not_checkable": b_availability_not_checkable,
    "availability_figure": b_availability_figure,
    "weather_report": b_weather_report,
    "land_cover_sentinel2": b_land_cover_sentinel2,
    "land_cover_s2_failure": b_land_cover_s2_failure,
    "land_cover_worldcover": b_land_cover_worldcover,
    "land_cover_brief": b_land_cover_brief,
    "terrain_summary": b_terrain_summary, "elevation_table": b_elevation_table,
    "slope_class_table": b_slope_class_table, "slope_class_figure": b_slope_class_figure,
    "water_classes_table": b_water_classes_table, "water_threshold_note": b_water_threshold_note,
    "aoi_admin_places": b_aoi_admin_places, "osm_counts_table": b_osm_counts_table,
    "osm_counts_missing": b_osm_counts_missing, "buildings_by_category": b_buildings_by_category,
    "water_influences_section": b_water_influences_section, "where_figure": b_where_figure,
    "mcdm_decision_matrix": b_mcdm_decision_matrix, "mcdm_matrix_figure": b_mcdm_matrix_figure,
    "mcdm_ranking": b_mcdm_ranking,
    "mcdm_closeness_figure": b_mcdm_closeness_figure,
    "sensitivity_sweep": b_sensitivity_sweep,
    "sensitivity_figure": b_sensitivity_figure,
    "hazard_product_mismatch": b_hazard_product_mismatch, "hazard_flood": b_hazard_flood,
    "depth_summary": b_depth_summary, "depth_band_table": b_depth_band_table,
    "depth_how_firm": b_depth_how_firm, "depth_bias": b_depth_bias,
    "climate_zone_statement": b_climate_zone,
    **{f"exposure_{s}": _exposure(s) for s in ("critical_infrastructure",)},
    **{f"evidence_{s}": _evidence(s) for s in ("critical_infrastructure",)},
    "exposure_roads_and_accessibility": _exposure("roads_and_accessibility"),
    "roads_summary": b_roads_summary, "roads_by_class_table": b_roads_by_class_table,
    "roads_passability_km": b_roads_passability_km, "roads_bridges": b_roads_bridges,
    "roads_hospital_access": b_roads_hospital_access, "slope_tree_notes": b_slope_tree_notes,
    "evidence_roads_and_accessibility": _evidence("roads_and_accessibility"),
    **{f"{kind}_{s}": maker(s) for s in ("food", "water", "health")
       for kind, maker in (("exposure", _exposure), ("evidence", _evidence))},
    "people_in_aoi_statement": b_people_in_aoi,
    "rests_on_where_am_i": b_rests_on_where_am_i,
    **{f"hev_map_{m}": _hev_map(m) for m in ("essential_services", "health")},
    **{f"hev_exposure_{m}": _hev_exposure_table(m) for m in ("essential_services", "health")},
    "hev_food_clusters": b_hev_food_clusters, "hev_people": b_hev_people,
    "hev_roads_bridges": b_hev_roads_bridges,
    "hev_population_map": b_hev_population_map,
    "hev_inform": b_hev_inform, "hev_previous_floods": b_hev_previous_floods,
    "hev_income_group": b_hev_income_group,
    "hev_inform_meters": _inform_figure("hev_inform_meters", "meters"),
    "hev_inform_trend": _inform_figure("hev_inform_trend", "trend"),
    "hev_inform_trend_line": b_hev_inform_trend_line,
    # Computed last (report_render.LAST).
    "data_acquired": b_data_acquired, "how_sure": b_how_sure,
}


def heading_source(slot: str, c: Ctx) -> str:
    """The "from <source>" of a module's heading, read from what it rests on
    (D-32: never a fixed phrase that could describe another event)."""
    inp = c.inp
    if slot == "data_availability":
        p = inp.get("availability") or {}
        labels = [pr.get("label") for pr in p.get("products") or [] if pr.get("label")]
        return f"the {', '.join(labels)} catalogues" if labels else ""
    if slot == "weather":
        src = ((inp.get("weather") or {}).get("meta") or {}).get("source") or {}
        if src.get("model_id"):
            return f"{src['model_id']} ({src.get('native_res_km')} km) via the Open-Meteo archive"
        # A legacy payload: the source it actually rests on (BUG-019).
        return "ECMWF IFS 9 km (not named in the request) via the Open-Meteo archive" \
            if src else ""
    if slot == "land_use_pre_event":
        if inp.get("lulc") is not None:
            return "Sentinel-2 L2A"
        wc = inp.get("worldcover")
        return f"ESA WorldCover {str((wc.get('source') or {}).get('time_start', ''))[:4]}" if wc else ""
    if slot == "terrain":
        return "the Copernicus DEM GLO-30" if inp.get("terrain") is not None else ""
    if slot == "water_and_structures":
        parts = [n for n, role in (("JRC Global Surface Water", "water"), ("OpenStreetMap", "where"))
                 if inp.get(role) is not None]
        return " and ".join(parts)
    if slot in ("mcdm_pre_ranking", "mcdm_ranking"):
        return "TOPSIS over the stored event indices" if inp.get("ranking") is not None else ""
    if slot == "sensitivity":
        return "a one-at-a-time weight sweep" if inp.get("sensitivity") is not None else ""
    if slot in ("essential_services_hev", "human_health_risks_hev"):
        extent = inp.get("extent")
        return f"the {extent.get('label')} flood extent and OpenStreetMap" if extent else ""
    return ""
