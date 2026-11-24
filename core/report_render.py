"""Modules 1-12, filled from stored results into their vault templates (CP7).

The templates are LIVE vault notes, "Module N - <title> template" in
`Head/Output templates` (TEMPLATES below); their fenced block is read at render
time. Modules 1-10 are the Flood analyst's, 11 the Public report, 12 the
Executive summary. Every placeholder but one kind is a FACT:

    {{fact:key}}  {{tile:key|..}}  {{meter:key|..}}  {{chip:key}}
    {{pictogram:key}}  {{figure:key}}  {{map:key}}      -> filled here, in Python
    {{llm:slot|max_words=N}}                             -> written by an agent

A fact is computed by one function in FACTS (or, for the paragraphs and
tables of Modules 1-10, in `core/module_blocks.BLOCKS`) from the store's cached inputs --
copied, never estimated -- and returns its value, unit, source and status. A
fact that cannot be filled renders as "Not known yet -- <reason>", and its
reason travels with it. **Every fact is also written to the facts sidecar**,
`<NN_slot>.facts.json`: key, value, unit, source, status. That file is RQ5's
facts layer (D-28): two runs agree when their sidecars agree.

**A report (11 or 12) needs a Flood analyst run first** (D-24): Modules 1-10 of the same
store -- same AOI, window and cache policy -- must be written, or the render
is refused. The flood extent is the best-ranked product's; a product that maps
no flood makes every flood-dependent fact NOT KNOWN for that reason.

`{{llm:...}}` slots are filled from `llm` (slot -> text) when given, each cut
to its word limit; otherwise they render as a visible pending marker and are
listed in the sidecar as pending. The pipelines (CP8) supply them.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import dating, report_config
from .numfmt import num
from .outputs import NOT_KNOWN, Inputs, best_ranked, depth_band_rows, depth_for, module_product

TEMPLATES = {"data_availability": "Module 1 - Data availability template",
             "weather": "Module 2 - Weather data template",
             "land_use_pre_event": "Module 3 - Land use before the event template",
             "terrain": "Module 4 - Terrain and slope classes template",
             "water_and_structures": "Module 5 - Permanent water and structures template",
             "mcdm_pre_ranking": "Module 6 - MCDM decision matrix template",
             "mcdm_ranking": "Module 7 - MCDM ranking template",
             "sensitivity": "Module 8 - Sensitivity analysis template",
             "essential_services_hev": "Module 9 - Essential services template",
             "human_health_risks_hev": "Module 10 - Human health risks template",
             "public_report": "Module 11 - Public report template",
             "executive_summary": "Module 12 - Executive summary template"}
REPORTS = ("public_report", "executive_summary")
REQUIRED = ("data_availability", "weather", "land_use_pre_event", "terrain",
            "water_and_structures", "mcdm_pre_ranking", "mcdm_ranking", "sensitivity",
            "essential_services_hev", "human_health_risks_hev")
PLACEHOLDER = re.compile(r"\{\{(fact|tile|meter|chip|pictogram|figure|map|llm):([a-z0-9_]+)((?:\|[^}]*)?)\}\}")
CHIPS = ("AFFECTED", "AT RISK", "NOT AFFECTED", "NOT OBSERVED", "NOT KNOWN")
EMPTY = "\x00empty\x00"             # what an empty block fills its placeholder with

# How a tile or meter is labelled when its fact carries no unit of its own.
LABELS = {
    "last_updated": "last updated", "flooded_pct": "of the observed land flooded",
    "inform_risk": "INFORM risk", "inform_hazard": "INFORM hazard and exposure",
    "inform_vulnerability": "INFORM vulnerability",
    "inform_lcc": "INFORM lack of coping capacity", "confidence": "confidence",
    "storm_total_mm": "storm total", "peak_1h_mm": "peak hour", "rain_hours": "hours of rain",
    "antecedent_phrase": "rain before the event", "max_gust_kmh": "strongest gust",
    "people_in_aoi": "people in the AOI", "people_in_flood_extent": "people in the flood",
    "flooded_km2": "flooded", "gdp_usd_bn": "GDP", "population_national": "population (national)",
    "roads_flooded_km": "road flooded", "hospitals_access_at_risk": "hospitals with access at risk",
    "farmland_flooded_km2": "farmland flooded",
    "flood_depth_m": "median depth, biased high",
    # Named in a merged "Not known yet" line (_merge_not_known).
    "hev_map_essential_services": "the map", "hev_map_health": "the map",
    "hev_exposure_essential_services": "the facility table",
    "hev_exposure_health": "the facility table", "hev_food_clusters": "the food clusters",
    "hev_people": "the population", "hev_population_map": "the population map",
}

# Marks a generic "Not known yet" line with the fact it stands for, so the
# lines one reason produced in a section can be merged into one.
NK_MARK = re.compile(r"\x00nk:([a-z0-9_]+)\x00")


def label(key: str) -> str:
    return LABELS.get(key, key.replace("_", " "))


@dataclass
class Fact:
    key: str
    value: Any = None
    unit: str = ""
    source: str = ""
    status: str = "ok"              # ok | not_known | pending | advisory
    reason: str = ""
    basis: str = "measured"         # measured | stated_by_source | assumed | config
    needs: str = ""                 # for not_known: what NEW DATA would fill it, in plain words
    run: list = None                # for not_known: tools still to run, by registered name
    text: str = ""                  # a rendered form, when the value is a list or a table
    # "data_source_needed": not known because NO source exists for this
    # country yet -- not a run that failed (BUG-050). Rendered as its own state.
    gap: str = ""

    def __post_init__(self) -> None:
        if self.run is None:
            self.run = []

    @property
    def known(self) -> bool:
        return self.status in ("ok", "advisory")


def nk(key: str, reason: str, needs: str = "") -> Fact:
    """A fact that is not known. `needs` may name tools (a run still to do)
    or data (something nobody has yet); the two are kept apart, because a
    reader asked "what would you need to say more" is owed only the second."""
    from . import workflows

    words = re.findall(r"[a-z][a-z0-9_]+", needs)
    tools = [t for t in words if workflows.available(t)]
    # A switched-off tool is never offered as a run still to do.
    off = [workflows.canonical(t) for t in words if workflows.canonical(t) in workflows.INACTIVE]
    if off and not tools:
        return Fact(key, status="not_known", reason=f"{reason} ({workflows.INACTIVE[off[0]]})")
    return Fact(key, status="not_known", reason=reason, needs="" if tools else needs, run=tools)


NEEDS_SOURCE = "Needs a data source for this country --"


def needs_source(key: str, reason: str, needs: str) -> Fact:
    """Not known because no data source covers this country yet (BUG-050)."""
    f = nk(key, reason, needs)
    f.gap = "data_source_needed"
    return f


def stale_population(pop: dict | None) -> bool:
    """A stored population payload from the retired census tool
    (`fetch_population`), which has no WorldPop `dataset` block. Its figures
    are not re-read: the reader says to run `get_pop_data` instead."""
    return bool(pop) and "dataset" not in pop


class Ctx:
    """What a fact function may read: the store's inputs, its settings, and
    the facts already computed (some facts are built from others)."""

    def __init__(self, store: Any):
        self.store = store
        self.inp = Inputs(store)
        self.settings = report_config.settings(getattr(store, "slug", ""))
        self.facts: dict[str, Fact] = {}
        self.figures: list[dict[str, Any]] = []
        self.pending_reasons: dict[str, str] = {}
        # The technical account of a failure (a request error, a vault
        # error), for the facts sidecar only -- the report says it plainly.
        self.details: dict[str, str] = {}
        # The pipeline level of the uncertainty register: how the run that
        # renders this went (core/pipelines/base.py). No pipeline, none.
        self.pipeline_items: list[dict] = []
        # What a module's blocks showed, in order, and the caveats they add:
        # its "When the data was acquired" and "How sure are we?" are built
        # from these (core/module_blocks.py), not from every input consulted.
        self.rests_on: list[tuple[str, Any]] = []
        self.sure_extra: list[str] = []
        # The reader's persona: sets each agent slot's word limit.
        self.persona = ""

    def rest(self, name: str, payload: Any) -> None:
        if all(n != name for n, _ in self.rests_on):
            self.rests_on.append((name, payload))

    def get(self, role: str) -> dict | None:
        return self.inp.get(role)

    def meta(self, field_name: str) -> str:
        path = getattr(self.store, "memory_path", None)
        if path is None or not Path(path).exists():
            return ""
        for line in Path(path).read_text(encoding="utf-8").splitlines()[:12]:
            if line.startswith(f"{field_name}:"):
                return line.split(":", 1)[1].strip()
        return ""

    def extent(self) -> tuple[dict | None, str]:
        """The module product's stored extent -- the user's choice, else the
        best-ranked (D-24, 2026-11-18) -- or None and why."""
        top, label, how = module_product(self.store, self.inp)
        if top is None:
            return None, f"{label}, so the best-ranked product is not known"
        ext = self.get("extent")
        if ext is None:
            return None, f"the flood extent of {label} is not stored (run get_flood_map)"
        if ext.get("product") != top:
            return None, (f"the stored extent is {ext.get('label')}, not the "
                          f"{'chosen' if how == 'chosen' else 'best-ranked'} {label}")
        if ext.get("status") == "no_flood_mapped":
            return None, f"{ext.get('label')} maps no flood over the AOI in the window (D-24)"
        if ext.get("status") != "flood_mapped":
            return None, ext.get("reason") or "the extent could not be evaluated"
        return ext, ""

    def observed(self) -> tuple[dict | None, str]:
        """As `extent`, but a product that READ the AOI and mapped no flood is
        returned too -- for what it saw: the hazard map, the facilities on
        observed and unobserved land, the people on land it did not see
        (user bug 2026-11-18). Flood figures keep using `extent`."""
        ext, why = self.extent()
        if ext is not None:
            return ext, ""
        held = self.get("extent")
        top, _, _ = module_product(self.store, self.inp)
        if (held is not None and top is not None and held.get("product") == top
                and held.get("status") == "no_flood_mapped"):
            return held, ""
        return None, why


def _round(v: Any, places: int = 2) -> Any:
    return round(v, places) if isinstance(v, float) else v


# -- the facts ----------------------------------------------------------------------
def f_event_name(c: Ctx) -> Fact:
    name = c.settings.get("event_name")
    return (Fact("event_name", name, source="core/report_config.py", basis="config")
            if name else Fact("event_name", c.store.slug, source="experiment slug", basis="config"))


def f_aoi(c: Ctx) -> Fact:
    place = c.meta("place")
    return Fact("aoi_display_name", place, source="event.md") if place else \
        nk("aoi_display_name", "the experiment records no place name")


def f_window(c: Ctx) -> Fact:
    w = c.meta("window")
    return Fact("window", w, source="event.md") if w else nk("window", "no window recorded")


def f_last_updated(c: Ctx) -> Fact:
    return Fact("last_updated", datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                source="render time (UTC)")


def f_verified_by(c: Ctx) -> Fact:
    who = c.settings.get("verified_by")
    return Fact("verified_by", who, source="core/report_config.py", basis="config") if who \
        else nk("verified_by", "no reviewer has signed this report yet", "a named reviewer")


def _weather(c: Ctx) -> tuple[dict | None, dict]:
    w = c.get("weather")
    return w, ((w or {}).get("event_summary") or {})


def f_storm_total(c: Ctx) -> Fact:
    w, es = _weather(c)
    v = (es.get("storm_total_mm") or {}).get("aoi_mean")
    return Fact("storm_total_mm", v, "mm storm total", "fetch_weather (AOI mean)") if v is not None else \
        nk("storm_total_mm", "the weather is not stored, or its total was withheld for gaps",
           "fetch_weather")


def f_peak_1h(c: Ctx) -> Fact:
    _, es = _weather(c)
    v = (es.get("max_intensity_mm") or {}).get("1h")
    return Fact("peak_1h_mm", v, "mm in the peak hour", "fetch_weather") if v is not None else \
        nk("peak_1h_mm", "no peak hourly rain is stored", "fetch_weather")


def f_rain_hours(c: Ctx) -> Fact:
    _, es = _weather(c)
    bursts = es.get("rain_bursts")
    if not bursts:
        return nk("rain_hours", "no rain bursts are stored", "fetch_weather")
    hours = 0.0
    for b in bursts:
        try:
            a = datetime.fromisoformat(b["start_utc"].replace("Z", "+00:00"))
            z = datetime.fromisoformat(b["end_utc"].replace("Z", "+00:00"))
            hours += (z - a).total_seconds() / 3600 + 1
        except (KeyError, ValueError):
            return nk("rain_hours", "a rain burst has no readable start or end")
    return Fact("rain_hours", round(hours), "hours of rain",
                "fetch_weather (sum of rain-burst durations)")


def f_antecedent(c: Ctx) -> Fact:
    _, es = _weather(c)
    v = (es.get("antecedent_api_mm") or {}).get("d30")
    if v is None:
        return nk("antecedent_phrase", "no antecedent rain index is stored", "fetch_weather")
    return Fact("antecedent_phrase", v, "mm in the 30 days before", "fetch_weather (antecedent index)")


def f_gust(c: Ctx) -> Fact:
    _, es = _weather(c)
    v = es.get("max_gust_ms")
    return Fact("max_gust_kmh", round(v * 3.6, 1), "km/h strongest gust",
                "fetch_weather (max gust, m/s x 3.6)") \
        if v is not None else nk("max_gust_kmh", "no gust is stored", "fetch_weather")


def _figure_path(c: Ctx, payload: dict | None, name: str) -> str:
    fig = (payload or {}).get("figure") or {}
    if fig.get("path") and Path(fig["path"]).is_file():
        return fig["path"]
    index = Path(c.store.dir) / "figures" / "index.json"
    if index.exists():
        for row in json.loads(index.read_text(encoding="utf-8")):
            if row.get("name") == name and Path(row.get("png", "")).is_file():
                return row["png"]
    return ""


def f_hyetograph(c: Ctx) -> Fact:
    path = _figure_path(c, c.get("weather"), "hyetograph")
    return Fact("rain_hyetograph", path, source="fetch_weather (hyetograph)") if path else \
        nk("rain_hyetograph", "the hyetograph has not been drawn", "fetch_weather")


def f_where_map(c: Ctx) -> Fact:
    path = _figure_path(c, c.get("where"), "where_am_i_map")
    return Fact("where_am_i_overview", path, source="where_am_i (OpenStreetMap)") if path else \
        nk("where_am_i_overview", "the overview map has not been drawn", "where_am_i")


def f_worst_localities(c: Ctx) -> Fact:
    return nk("worst_localities_bar", "flooded area and depth by locality need locality "
              "boundaries, which are not computed yet", "locality boundaries")


# -- flood depth (calc_flood_depth, BUG-012) ----------------------------------------------
def _depth(c: Ctx) -> tuple[dict | None, str]:
    ext, why = c.extent()
    if not ext:
        return None, why
    dep, why = depth_for(c.inp, ext)
    return dep, why


def _depth_nk(key: str, why: str) -> Fact:
    return nk(key, why, "calc_flood_depth" if "not computed" in why else "")


def f_flood_depth(c: Ctx) -> Fact:
    dep, why = _depth(c)
    if not dep:
        return _depth_nk("flood_depth_m", why)
    d = dep["depth"]
    bias = (dep.get("bias") or {}).get("label", "biased high")
    return Fact("flood_depth_m", d["median_m"], f"m median depth ({bias})",
                f"calc_flood_depth (FwDET v2.1 on GLO-30, {dep.get('product')}; 95th percentile "
                f"{d['p95_m']} m; {bias}, BUG-014)", basis="assumed")


def f_depth_bands(c: Ctx) -> Fact:
    from .tables import Table

    dep, why = _depth(c)
    if not dep:
        return _depth_nk("depth_bands_table", why)
    rows = depth_band_rows(dep)
    t = Table("depth_bands", "How deep the flood is",
              ["Band", "Depth", "km2", "Share of the flood", "Band stable"], rows,
              caption="Depths are biased high (the DEM includes trees and buildings), so an area "
              "may be one band shallower than shown. Vehicle classes: Kramer et al. (2016). Band "
              "stable: share of the band's area whose band holds in more than half the depth "
              "error realisations.")
    return Fact("depth_bands_table", rows, source="calc_flood_depth", text=t.to_markdown())


def f_depth_uncertainty(c: Ctx) -> Fact:
    dep, why = _depth(c)
    if not dep:
        return _depth_nk("depth_uncertainty", why)
    e, b = dep["error"], dep.get("boundary") or {}
    text = (f"Depth is biased high: the DEM includes trees and buildings, and where they line "
            f"the flood they lift the water surface. The dry edge stands "
            f"{b.get('edge_above_flood_ground_m')} m above the flooded ground (medians); how much of "
            "that is canopy or roof is not measured, so the bias has a direction but no size. "
            f"Across {e['realisations']} DEM perturbations x 3 boundaries the median spread is "
            f"{e['median_sd_m']} m, and {round(e['unstable_share_vehicle'] * 100, 1)}% of the flood may "
            "change vehicle class. No observed depth exists to check it against.")
    return Fact("depth_uncertainty", e["median_sd_m"], "m median spread", "calc_flood_depth (error method)",
                text=text, basis="measured")


def f_roads_passability(c: Ctx) -> Fact:
    ext, why = c.extent()
    r = c.get("roads")
    if not ext or not r:
        return nk("roads_passability", why or "road access is not stored", "assess_road_access")
    km = r.get("passability_km")
    if not km:
        dep, dwhy = _depth(c)
        reason = ("road access was checked before the depth was computed; it needs re-running"
                  if dep else dwhy)
        return nk("roads_passability", reason, "assess_road_access" if dep else "calc_flood_depth")
    text = "; ".join(f"{k}: {v} km" for k, v in km.items())
    if r.get("passability_unstable_km") is not None:
        text += f" ({r['passability_unstable_km']} km may change class)"
    text += (". Depth is biased high, so a road may be one class more passable than shown")
    return Fact("roads_passability", km, "km of flooded road by depth class",
                "assess_road_access x calc_flood_depth (Kramer et al. 2016)", text=text)


def f_depth_map(c: Ctx) -> Fact:
    dep, why = _depth(c)
    if not dep:
        return _depth_nk("depth_map", why)
    path = _draw_depth(dep, Path(c.store.dir) / "figures" / "flood_depth.png")
    c.figures.append({"name": "flood_depth", "png": str(path),
                      "caption": f"Flood depth (FwDET v2.1 on GLO-30), {dep.get('product')} extent."})
    return Fact("depth_map", str(path), source="calc_flood_depth")


def f_advisory(c: Ctx) -> Fact:
    return Fact("advisory_10cm", "ADVISORY", source="design decision D-16", status="advisory",
                basis="stated_by_source")


def _facilities(c: Ctx) -> dict:
    return ((c.get("where") or {}).get("facilities") or {}).get("categories") or {}


def _no_facilities(c: Ctx) -> str:
    """Why the buildings of interest are not located: a failed request is
    said as one, so an outage is never read as an empty AOI."""
    where = c.get("where")
    if where is None:
        return "the area overview has not been run"
    if "facilities" not in where:
        return "the area overview was run before facilities were located; it needs re-running"
    problem = (where.get("counts") or {}).get("problem") or ""
    if problem:
        c.details["facilities"] = problem
        return ("the map service (OpenStreetMap) did not answer when the facilities were "
                "requested; this section fills when the area overview is re-run")
    return "no facility was located"


BUILDINGS_OF_INTEREST = ("hospitals", "schools", "water_facilities", "wastewater_facilities",
                         "marketplaces", "electric_substations", "government")


def f_boi_counts(c: Ctx) -> Fact:
    cats = _facilities(c)
    if not cats:
        return nk("buildings_of_interest_counts", _no_facilities(c), "where_am_i (re-run)")
    from .workflows.osm_context import category_label

    counts = {category_label(k): cats[k]["count"] for k in BUILDINGS_OF_INTEREST if k in cats}
    return Fact("buildings_of_interest_counts", counts, "mapped", "where_am_i (OpenStreetMap)",
                text=" · ".join(f"{k} {v}" for k, v in counts.items()))


def f_boi_list(c: Ctx) -> Fact:
    cats = _facilities(c)
    if not cats:
        return nk("buildings_of_interest_list", _no_facilities(c), "where_am_i (re-run)")
    names = [i["name"] for k in ("hospitals", "water_facilities", "schools") if k in cats
             for i in cats[k]["items"] if i.get("name")]
    shown = names[:8]
    text = ", ".join(shown) + (f", and {len(names) - 8} more" if len(names) > 8 else "")
    return Fact("buildings_of_interest_list", shown, source="where_am_i (OpenStreetMap)",
                text=text or "none named")


def f_people_aoi(c: Ctx) -> Fact:
    pop = c.get("population")
    if not pop:
        return nk("people_in_aoi", "the AOI population is not stored", "get_pop_data")
    if stale_population(pop):
        return nk("people_in_aoi", "the stored population came from the retired census tool",
                  "get_pop_data (WorldPop)")
    lo, hi = (pop.get("range") or [None, None])[:2]
    return Fact("people_in_aoi", pop["population"], "people in the AOI",
                f"get_pop_data ({pop.get('vintage')}; pixel sum, range {lo:,} to {hi:,})")


def f_people_extent(c: Ctx) -> Fact:
    """People inside the mapped flood extent -- "population affected" (user
    decision 2026-11-02): get_pop_data run with the extent raster, for THIS
    extent; a count made over another product's extent is not this one's."""
    ext, why = c.extent()
    if not ext:
        return nk("people_in_flood_extent", why, "get_flood_map")
    pop = c.get("population")
    flood = (pop or {}).get("flood_extent") or {}
    if not pop or stale_population(pop) or not flood:
        return nk("people_in_flood_extent", "the people inside the flood extent are not counted "
                  "yet", "get_pop_data with the flood extent raster")
    if flood.get("extent_raster") != (ext.get("raster") or {}).get("path"):
        return nk("people_in_flood_extent", "the stored count is for another flood extent",
                  "get_pop_data with the flood extent raster")
    return Fact("people_in_flood_extent", flood["people_in_flood"], "people in the flood",
                f"get_pop_data x get_flood_map ({pop.get('vintage')}; {ext.get('label')})")


def f_flooded_km2(c: Ctx) -> Fact:
    ext, why = c.extent()
    return Fact("flooded_km2", ext["flood_km2"], "km2 flooded", f"get_flood_map ({ext['label']})") \
        if ext else nk("flooded_km2", why, "get_flood_map")


def f_flooded_pct(c: Ctx) -> Fact:
    ext, why = c.extent()
    if not ext or ext.get("flood_fraction_of_observed") is None:
        return nk("flooded_pct", why or "no observed share", "get_flood_map")
    return Fact("flooded_pct", round(ext["flood_fraction_of_observed"] * 100, 6), "% of observed land",
                f"get_flood_map ({ext['label']})")


def f_land_donut(c: Ctx) -> Fact:
    return nk("land_cover_donut", "the land cover of the flooded land is not computed yet",
              "the land cover inside the flood extent")


def f_country(c: Ctx) -> Fact:
    country = ((c.get("inform") or {}).get("country") or ((c.get("where") or {}).get("admin") or {})
               .get("country"))
    return Fact("country", country, source="fetch_inform_risk / where_am_i") if country else \
        nk("country", "no country is stored")


def _inform(key: str, ind: str) -> Callable[[Ctx], Fact]:
    def fn(c: Ctx) -> Fact:
        inf = c.get("inform")
        s = ((inf or {}).get("scores") or {}).get(ind) or {}
        if s.get("value") is None:
            return nk(key, "INFORM is not stored", "fetch_inform_risk")
        rel = inf.get("release") or {}
        return Fact(key, s["value"], "of 10", f"{rel.get('name')} (published {rel.get('published')}, national)")
    return fn


def f_gdp(c: Ctx) -> Fact:
    g = (((c.get("wb") or {}).get("indicators") or {}).get("gdp_usd") or {})
    return Fact("gdp_usd_bn", round(g["value"] / 1e9, 1), "US$ bn GDP", f"World Bank {g.get('year')} (national)") \
        if g.get("value") else nk("gdp_usd_bn", "World Bank indicators are not stored", "fetch_world_bank_indicators")


def f_pop_national(c: Ctx) -> Fact:
    g = (((c.get("wb") or {}).get("indicators") or {}).get("population") or {})
    return Fact("population_national", g["value"], "people (national)", f"World Bank {g.get('year')}") \
        if g.get("value") else nk("population_national", "World Bank indicators are not stored",
                                  "fetch_world_bank_indicators")


def f_cutoff(c: Ctx) -> Fact:
    d = c.get("disasters")
    return Fact("search_cutoff", d["search_cutoff"], source="search_previous_disasters") \
        if d and d.get("search_cutoff") else nk("search_cutoff", "previous disasters are not stored",
                                                 "search_previous_disasters")


def f_disasters_timeline(c: Ctx) -> Fact:
    d = c.get("disasters")
    if not d or not d.get("events"):
        return nk("previous_disasters_timeline", "previous disasters are not stored",
                  "search_previous_disasters")
    path = _draw_timeline(d, Path(c.store.dir) / "figures" / "previous_disasters_timeline.png",
                          dating.store_zone(c.store))
    via = ", ".join(sorted({e.get("source", "") for e in d["events"]} - {""})) or "GDACS"
    c.figures.append({"name": "previous_disasters_timeline", "png": str(path),
                      "caption": f"Floods and storms recorded before {d['search_cutoff']} ({via})."})
    return Fact("previous_disasters_timeline", str(path), source=f"search_previous_disasters ({via})")


def f_flood_overlay(c: Ctx) -> Fact:
    """The flood overlay (BUG-021): the best-ranked product's extent on
    OpenStreetMap with the AOI box, places and buildings of interest, as
    get_flood_map drew it. Shown whether or not the product mapped flood --
    "no flood mapped, 14% of the land observed" is a finding, and the grey of
    the unobserved land is what says how far to trust it. With no flood map at
    all, the plain OSM overview is shown and the reason is printed under it."""
    top, label, _ = module_product(c.store, c.inp)
    ext = c.get("extent")
    fig = ((ext or {}).get("figure") or {}).get("path", "")
    if top is not None and ext and ext.get("product") == top and fig and Path(fig).is_file():
        c.figures.append({"name": "flood_overlay", "png": fig,
                          "caption": ext["figure"].get("caption", "")})
        return Fact("flood_overlay", fig, source=f"get_flood_map ({ext['label']})")
    found, why = c.extent()
    if found and (found.get("raster") or {}).get("path"):
        # An extent stored before the overlay existed: the plain extent map.
        path = _draw_extent(found, Path(c.store.dir) / "figures" / "flood_overlay.png")
        c.figures.append({"name": "flood_overlay", "png": str(path),
                          "caption": f"{found['label']} flood extent over the AOI."})
        return Fact("flood_overlay", str(path), source=f"get_flood_map ({found['label']})")
    why = why or (ext or {}).get("reason") or ((ext or {}).get("map") or {}).get("problem") \
        or "the flood overlay has not been drawn"
    osm = _figure_path(c, c.get("where"), "where_am_i_map")
    if osm:
        return Fact("flood_overlay", osm, source="where_am_i (OpenStreetMap)", status="advisory",
                    reason=f"The flood extent is not drawn on this map: {why}.")
    return nk("flood_overlay", why, "get_flood_map")


def f_confidence(c: Ctx) -> Fact:
    ext, why = c.extent()
    if not ext:
        return nk("confidence", why, "get_flood_map")
    cov = ext.get("coverage") or 0
    band = "high" if cov >= 0.9 else "medium" if cov >= 0.6 else "low"
    return Fact("confidence", band, f"confidence: the product observed {cov * 100:.0f}% of the "
                "land (high from 90%, medium from 60%)",
                "rule: extent coverage >= 0.9 high, >= 0.6 medium, else low", basis="assumed")


def f_flood_product(c: Ctx) -> Fact:
    top, label, how = module_product(c.store, c.inp)
    if not top:
        return nk("flood_product", label, "rank_flood_products")
    return Fact("flood_product", label, source=("the user's choice" if how == "chosen"
                                                else "rank_flood_products (best-ranked)"))


def f_top_uncertainty(c: Ctx) -> Fact:
    ext = c.get("extent")
    for item in (((ext or {}).get("uncertainty") or {}).get("tool") or []):
        if item.get("key") in ("no_flood", "coverage"):
            return Fact("top_uncertainty", item["statement"], source="get_flood_map uncertainty",
                        basis=item.get("basis", "measured"))
    return nk("top_uncertainty", "the flood extent's uncertainty is not stored", "get_flood_map")


def f_followups(c: Ctx) -> Fact:
    return Fact("followup_chips", ["what caused it", "where it flooded", "people and land",
                                   "vulnerability", "how sure"], source="template sections",
                text="what caused it · where it flooded · people and land · vulnerability · how sure")


def f_sources(c: Ctx) -> Fact:
    seen, out = set(), []
    for name in c.inp.used:
        payload = (c.store.cached_input(name) or {}).get("payload") or {}
        for s in payload.get("sources") or []:
            if isinstance(s, dict) and s.get("role", "major") == "major" and s.get("title") \
                    and s["title"] not in seen:
                seen.add(s["title"])
                when = s.get("vintage") or (f"retrieved {s['retrieved_at'][:10]}"
                                            if s.get("retrieved_at") else "")
                out.append(f"{s['title']} ({when})".replace(" ()", ""))
    return Fact("sources_major", out[:5], source="the stored results' sources blocks",
                text="; ".join(out[:5]) or "none recorded") if out else \
        nk("sources_major", "no stored result carries a sources block")


# -- Executive summary facts ----------------------------------------------------------
def f_roads_km(c: Ctx) -> Fact:
    ext, why = c.extent()
    r = c.get("roads")
    if not ext or not r:
        return nk("roads_flooded_km", why or "road access is not stored", "assess_road_access")
    return Fact("roads_flooded_km", (r.get("roads_km_total") or {}).get("flooded_km"), "km of road in the flood",
                "assess_road_access (OpenStreetMap)")


def f_hosp_risk(c: Ctx) -> Fact:
    ext, why = c.extent()
    r = c.get("roads")
    if not ext or not r:
        return nk("hospitals_access_at_risk", why or "road access is not stored", "assess_road_access")
    return Fact("hospitals_access_at_risk", r.get("hospitals_access_at_risk"),
                f"of {len(r.get('hospitals') or [])} hospitals, access at risk",
                "assess_road_access (flooded road within the assumed distance)", basis="assumed")


def _in_extent(c: Ctx, keys: tuple[str, ...]) -> dict | None:
    from .workflows.impact import facilities_in_extent

    ext, _ = c.extent()
    where = c.get("where")
    if not ext or not where or not _facilities(c) or not (ext.get("raster") or {}).get("path"):
        return None
    return facilities_in_extent(where, ext["raster"]["path"], keys)


def _chip(key: str, keys: tuple[str, ...], *, roads: bool = False) -> Callable[[Ctx], Fact]:
    rule = ("AFFECTED: one in the flood extent; AT RISK: none in it, a flooded road within the "
            "assumed distance of one; NOT AFFECTED: the extent was observed and none is reached; "
            "NOT OBSERVED: every located facility sits on ground the product did not observe; "
            "NOT KNOWN: no mapped extent or nothing located")

    def fn(c: Ctx) -> Fact:
        ext, why = c.extent()
        if not ext:
            return Fact(key, "NOT KNOWN", source=rule, status="ok", reason=why, basis="assumed")
        if roads:
            r = c.get("roads")
            if not r:
                return Fact(key, "NOT KNOWN", source=rule, reason="road access is not stored", basis="assumed")
            km = (r.get("roads_km_total") or {}).get("flooded_km") or 0
            return Fact(key, "AFFECTED" if km > 0 else "NOT AFFECTED", source=rule, basis="assumed")
        hits = _in_extent(c, keys)
        if hits is None:
            return Fact(key, "NOT KNOWN", source=rule, reason=_no_facilities(c), basis="assumed")
        flooded = sum(v["flooded"] for v in hits.values())
        if flooded:
            return Fact(key, "AFFECTED", source=rule, basis="assumed")
        located = sum(v["located"] for v in hits.values())
        if located and not sum(v["dry"] for v in hits.values()):
            # None of them was seen: not observed is never "not affected"
            # (user decision 2026-10-26, BUG-025).
            return Fact(key, "NOT OBSERVED", source=rule, basis="measured",
                        reason=f"none of the {located} located facilities was observed by "
                               f"{ext.get('label')} in the window")
        r = c.get("roads")
        risky = any(h.get("access_at_risk") for h in (r or {}).get("hospitals") or []) \
            if "hospitals" in keys else False
        return Fact(key, "AT RISK" if risky else "NOT AFFECTED", source=rule, basis="assumed")
    return fn


def f_hospital_table(c: Ctx) -> Fact:
    from .tables import Table

    r = c.get("roads")
    ext, why = c.extent()
    if not r or not ext:
        return nk("hospital_access_table", why or "road access is not stored", "assess_road_access")
    hospitals = r.get("hospitals") or []
    rows = [[h.get("name") or "unnamed hospital", h.get("site"), h.get("nearest_flooded_road_m"),
             "yes" if h.get("access_at_risk") else "no"] for h in hospitals]
    hit = [row for row, h in zip(rows, hospitals)
           if h.get("access_at_risk") or h.get("site") == "in the flood"]
    if not hit:
        # Nothing reached: one line, not a row per hospital (user decision
        # 2026-10-26). The counts keep "not observed" apart from "dry".
        sites = [h.get("site") for h in hospitals]
        dry, unseen = sites.count("dry"), sum(1 for x in sites if x not in ("dry", "in the flood"))
        return Fact("hospital_access_table", [], source="assess_road_access",
                    text=f"No major facilities affected ({len(hospitals)} hospitals checked: "
                         f"{dry} on observed dry ground, {unseen} on ground {ext.get('label')} "
                         "did not observe; none with a flooded road within the assumed distance).")
    t = Table("hospital_access", "Hospitals in the flood or with access at risk",
              ["Hospital", "Site", "Nearest flooded road (m)", "Access at risk"], hit)
    return Fact("hospital_access_table", hit, source="assess_road_access",
                text=t.to_markdown() + f"{chr(10)}The other {len(rows) - len(hit)} hospitals: "
                     "not in the flood and no flooded road within the assumed distance.")


def f_bridges(c: Ctx) -> Fact:
    r = c.get("roads")
    ext, why = c.extent()
    if not r or not ext:
        return nk("bridges_affected", why or "road access is not stored", "assess_road_access")
    from .outputs import _bridges_line
    from .workflows.impact import group_bridges

    b = r.get("bridges_in_flood_extent") or []
    g = group_bridges(b)
    return Fact("bridges_affected", g["named_count"], "named bridges in the flood extent",
                "assess_road_access (OSM bridge segments grouped by name)", text=_bridges_line(b))


def f_slope_tree(c: Ctx) -> Fact:
    st = c.get("slope_tree")
    if not st:
        return nk("slope_tree_road_notes", "the slope-and-tree check is not stored", "check_slope_tree_roads")
    notes = st.get("notes") or []
    text = (f"Slope-and-tree notes (not a forecast): {st.get('finding')}."
            + "".join(f"{chr(10)}- {n['road']} ({n['class']}), {n['length_m']} m" for n in notes[:8]))
    return Fact("slope_tree_road_notes", len(notes), "road segments", "check_slope_tree_roads",
                text=text, basis="assumed")


def _facility_runs(extent_problem: str) -> str:
    """The run a facility fact waits on: the extent when that is what is
    missing, else the area overview -- never one already stored."""
    return "get_flood_map" if extent_problem else "where_am_i (re-run)"


def f_food(c: Ctx) -> Fact:
    hits = _in_extent(c, ("food_grocery_shops", "marketplaces"))
    if hits is None:
        ext, why = c.extent()
        return nk("food_outlets_flooded", why or _no_facilities(c), _facility_runs(why))
    text = " · ".join(f"{k.replace('_', ' ')}: {v['flooded']} of {v['located']} in the flood" for k, v in hits.items())
    if not any(v["flooded"] for v in hits.values()):
        located = sum(v["located"] for v in hits.values())
        dry = sum(v["dry"] for v in hits.values())
        ext, _ = c.extent()
        text = (f"No major facilities affected ({located} food outlets located: {dry} on observed "
                f"dry ground, {located - dry} on ground {(ext or {}).get('label', 'the product')} "
                "did not observe).")
    return Fact("food_outlets_flooded", {k: v["flooded"] for k, v in hits.items()}, "flooded",
                "where_am_i x get_flood_map", text=text)


def f_farmland(c: Ctx) -> Fact:
    return nk("farmland_flooded_km2", "cropland inside the flood extent is not computed; crop "
              "type and stage are not known", "the cropland inside the flood extent, and a crop calendar")


def _checklist(key: str, keys: tuple[str, ...], label: str) -> Callable[[Ctx], Fact]:
    def fn(c: Ctx) -> Fact:
        from .workflows.impact import _values_at, FLOODED, DRY, PERMANENT
        import numpy as np

        ext, why = c.extent()
        cats = _facilities(c)
        if not ext or not cats:
            return nk(key, why or _no_facilities(c), _facility_runs(why if not ext else ""))
        items = [(k, i) for k in keys if k in cats for i in cats[k]["items"]]
        if not items:
            return Fact(key, [], source="where_am_i", text=f"No {label} are mapped in the AOI.")
        vals = _values_at(ext["raster"]["path"], np.array([i["lon"] for _, i in items]),
                          np.array([i["lat"] for _, i in items]), 255)
        dry = int(np.isin(vals, (DRY, PERMANENT)).sum())
        flooded = [(k, i) for (k, i), v in zip(items, vals) if v == FLOODED]
        unseen = len(items) - dry - len(flooded)
        counts = (f"{len(items)} located: {dry} on observed dry ground, {unseen} on ground "
                  f"{ext.get('label')} did not observe")
        if not flooded:
            # Nothing reached: one line, not one per facility (user decision
            # 2026-10-26). The counts keep "not observed" apart from "dry".
            return Fact(key, [], source="where_am_i x get_flood_map",
                        text=f"No major facilities affected ({counts}).")
        rows = [f"- [x] {i.get('name') or k.replace('_', ' ')} "
                f"({(i.get('tags') or {}).get('man_made') or (i.get('tags') or {}).get('power') or k.replace('_', ' ')}): "
                "in the flood" for k, i in flooded]
        return Fact(key, [r[6:] for r in rows], source="where_am_i x get_flood_map",
                    text=chr(10).join(rows + [f"The rest: {counts}."]))
    return fn


def f_health(c: Ctx) -> Fact:
    from .outputs import _note_status

    ok, detail = _note_status("health")
    if not ok:
        c.details["health"] = detail
        return nk("health_risk_list", "no sourced evidence yet: the health evidence note is not "
                  "written yet", "the health evidence note and its sources")
    d = c.store.deliverable("human_health_risk")
    return Fact("health_risk_list", "see text", source=f"vault note {detail}",
                text=(d or {}).get("text") or f"{NOT_KNOWN} the flood analyst agent's health flow has not run.")


def f_traffic(c: Ctx) -> Fact:
    from .tables import Table
    from .uncertainty import LEVELS

    rows = []
    for lvl in LEVELS:
        items = [i for name in c.inp.used for i in
                 ((((c.store.cached_input(name) or {}).get("payload") or {}).get("uncertainty") or {}).get(lvl) or [])
                 if isinstance(i, dict)]
        if lvl == "pipeline":
            items += [i for i in c.pipeline_items if isinstance(i, dict)]
        assumed = sum(1 for i in items if i.get("basis") == "assumed")
        light = "grey (nothing recorded)" if not items else "amber" if assumed else "green"
        rows.append([lvl, len(items), assumed, light])
    t = Table("uncertainty_traffic_lights", "Uncertainty by level",
              ["Level", "Items", "Assumed", "Light"], rows,
              caption="Green: every item measured or stated by its source; amber: at least one "
              "assumed; grey: nothing recorded at that level.")
    return Fact("uncertainty_traffic_lights", rows, source="the stored results' uncertainty blocks",
                text=t.to_markdown(), basis="assumed")


def f_new_data(c: Ctx) -> Fact:
    """New data, in plain words -- and, apart, the runs still to do. A run
    that has not happened is not data the world lacks."""
    from .plain_language import workflow_name

    needs = sorted({f.needs for f in c.facts.values() if f.status == "not_known" and f.needs})
    runs = sorted({workflow_name(t) for f in c.facts.values() if f.status == "not_known"
                   for t in f.run})
    text = chr(10).join(f"- {n}" for n in needs) or "No new data is needed beyond the runs below."
    if runs:
        text += chr(10) + chr(10) + "Still to run in this analysis: " + ", ".join(runs) + "."
    return Fact("new_data_needed", needs, source="the facts marked NOT KNOWN in this report",
                text=text)


FACTS: dict[str, Callable[[Ctx], Fact]] = {
    "event_name": f_event_name, "aoi_display_name": f_aoi, "window": f_window,
    "last_updated": f_last_updated, "verified_by": f_verified_by,
    "storm_total_mm": f_storm_total, "peak_1h_mm": f_peak_1h, "rain_hours": f_rain_hours,
    "antecedent_phrase": f_antecedent, "max_gust_kmh": f_gust, "rain_hyetograph": f_hyetograph,
    "where_am_i_overview": f_where_map, "worst_localities_bar": f_worst_localities,
    "advisory_10cm": f_advisory, "buildings_of_interest_counts": f_boi_counts,
    "buildings_of_interest_list": f_boi_list, "people_in_aoi": f_people_aoi,
    "people_in_flood_extent": f_people_extent, "flooded_km2": f_flooded_km2,
    "flooded_pct": f_flooded_pct, "land_cover_donut": f_land_donut, "country": f_country,
    "inform_risk": _inform("inform_risk", "inform_risk"),
    "inform_hazard": _inform("inform_hazard", "hazard_exposure"),
    "inform_vulnerability": _inform("inform_vulnerability", "vulnerability"),
    "inform_lcc": _inform("inform_lcc", "lack_of_coping_capacity"),
    "gdp_usd_bn": f_gdp, "population_national": f_pop_national, "search_cutoff": f_cutoff,
    "previous_disasters_timeline": f_disasters_timeline, "flood_overlay": f_flood_overlay,
    "confidence": f_confidence, "flood_product": f_flood_product,
    "top_uncertainty": f_top_uncertainty, "followup_chips": f_followups,
    "roads_flooded_km": f_roads_km, "hospitals_access_at_risk": f_hosp_risk,
    "status_roads": _chip("status_roads", ("hospitals",), roads=True),
    "status_food": _chip("status_food", ("food_grocery_shops", "marketplaces")),
    "status_water": _chip("status_water", ("water_facilities",)),
    "status_health": _chip("status_health", ("hospitals",)),
    "status_services": _chip("status_services", ("electric_substations", "schools", "government")),
    "roads_hospitals": f_flood_overlay, "hospital_access_table": f_hospital_table,
    "bridges_affected": f_bridges, "slope_tree_road_notes": f_slope_tree,
    "food_outlets_flooded": f_food, "farmland_flooded_km2": f_farmland,
    "water_facilities_checklist": _checklist("water_facilities_checklist", ("water_facilities",),
                                             "drinking water facilities"),
    "services_checklist": _checklist("services_checklist", ("electric_substations", "schools",
                                                            "government"), "essential services"),
    "health_risk_list": f_health, "uncertainty_traffic_lights": f_traffic,
    "flood_depth_m": f_flood_depth, "depth_bands_table": f_depth_bands,
    "depth_uncertainty": f_depth_uncertainty, "roads_passability": f_roads_passability,
    "depth_map": f_depth_map,
    # Computed last: it reads the other facts' reasons.
    "sources_major": f_sources, "new_data_needed": f_new_data,
}
LAST = ("sources_major", "new_data_needed", "data_acquired", "how_sure")

# Facts about THE REPORT, not about the event: when it was rendered, and the
# summaries built from the report's own other facts and inputs (its sources,
# what it still needs, its uncertainty lights). Two modes' reports hold
# different facts, so these legitimately differ between them; every other
# fact must not (D-3, tests/test_mode_consistency.py).
# `data_dated` dates the inputs THIS report used, and the two reports use
# different inputs, so it is report-scoped like the render time.
REPORT_SCOPED = ("last_updated", "uncertainty_traffic_lights", "data_dated") + LAST


# -- figures the reports draw (values copied from the payloads) ------------------------
def _provenance_foot(fig, line: str) -> None:
    """The figure's source and acquisition date, under it (BUG-020)."""
    import textwrap

    fig.text(0.01, -0.01, textwrap.fill(line, 150), fontsize=6.8, color="#333333",
             va="top")


def _draw_extent(ext: dict, png: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    with rasterio.open(ext["raster"]["path"]) as src:
        arr = src.read(1)
    shown = np.full(arr.shape, 0, dtype="uint8")
    shown[arr == 0] = 1
    shown[arr == 2] = 2
    shown[arr == 1] = 3
    cmap = ListedColormap(["#ffffff", "#e9e4d8", "#7fb3d5", "#1f4e9e"])
    fig, ax = plt.subplots(figsize=(8, 8 * arr.shape[0] / max(1, arr.shape[1]) + 0.6), dpi=130)
    fig.patch.set_facecolor("white")
    ax.imshow(shown, cmap=cmap, vmin=0, vmax=3, interpolation="nearest")
    ax.set_axis_off()
    ax.set_title(f"{ext['label']}: {ext['flood_km2']} km2 flooded, "
                 f"{ext['coverage'] * 100:.0f}% of the land observed", fontsize=10)
    ax.legend(handles=[Patch(color="#1f4e9e", label="flooded"), Patch(color="#7fb3d5", label="permanent water"),
                       Patch(color="#e9e4d8", label="dry (observed)"),
                       Patch(facecolor="#ffffff", edgecolor="#999999", label="not observed")],
              loc="lower left", fontsize=8, framealpha=0.9)
    from .figures import _provenance

    _provenance_foot(fig, _provenance(Path(ext["raster"]["path"])))
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return png


def _draw_depth(dep: dict, png: Path) -> Path:
    """The depth raster, banded by the vehicle classes (D-36), white backdrop."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    with rasterio.open(dep["depth"]["raster"]["path"]) as src:
        arr = src.read(1).astype(float)
        arr[arr == src.nodata] = np.nan
    edges = [0.0, 0.1, 0.3, 0.6, 1e9]
    colours = ["#cfe3f3", "#7fb3d5", "#2f6fae", "#0b2f5c"]
    labels = ["under 0.1 m", "0.1-0.3 m (car limit 0.3)", "0.3-0.6 m (emergency limit 0.6)", "over 0.6 m"]
    fig, ax = plt.subplots(figsize=(8, 8 * arr.shape[0] / max(1, arr.shape[1]) + 0.6), dpi=130)
    fig.patch.set_facecolor("white")
    ax.imshow(np.ma.masked_invalid(arr), cmap=ListedColormap(colours),
              norm=BoundaryNorm(edges, 4), interpolation="nearest")
    ax.set_axis_off()
    ax.set_title(f"Flood depth, FwDET v2.1 on GLO-30 -- median {dep['depth']['median_m']} m, "
                 "BIASED HIGH (the DEM includes trees and buildings)", fontsize=9)
    ax.legend(handles=[Patch(color=c, label=l) for c, l in zip(colours, labels)],
              loc="lower left", fontsize=8, framealpha=0.9)
    from .figures import _provenance

    _provenance_foot(fig, _provenance(Path(dep["depth"]["raster"]["path"])))
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return png


def _draw_timeline(d: dict, png: Path, zone: str | None = None) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    events = [e for e in d.get("events") or [] if e.get("start")]
    colours = {"red": "#d62728", "orange": "#ff7f0e", "green": "#2ca02c"}
    fig, ax = plt.subplots(figsize=(9, 2.4), dpi=130)
    fig.patch.set_facecolor("white")
    # Tall = floods and storms: the same selection as the line the HEV modules
    # print above this figure (module_blocks.b_hev_previous_floods), so the
    # two counts agree.
    tall = {"flood", "storm", "cyclone", ""}
    for e in events:
        when = datetime.fromisoformat(e["start"])
        ax.plot([when, when], [0, 1 if e.get("kind", "") in tall else 0.6],
                color=colours.get(e.get("alert_level", ""), "#888888"), lw=2)
    ax.set_yticks([])
    ax.set_ylim(0, 1.15)
    via = ", ".join(sorted({e.get("source", "") for e in events} - {""})) or "GDACS"
    n_tall = sum(1 for e in events if e.get("kind", "") in tall)
    ax.set_title(f"{n_tall} floods and storms (tall) and {len(events) - n_tall} other events (short) "
                 f"recorded before {d['search_cutoff']} ({via}); colour = GDACS alert level, "
                 "grey = none", fontsize=9)
    for side in ("left", "right", "top"):
        ax.spines[side].set_visible(False)
    from . import dating

    _provenance_foot(fig, dating.figure_line_for("search_previous_disasters", d, zone))
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    return png


# -- rendering -----------------------------------------------------------------------------
def template(slot: str) -> str:
    """The fenced block of the slot's live vault template."""
    from . import vault

    note = vault.read_note(vault.build_index(), TEMPLATES[slot])
    match = re.search(r"## Template\s*\n```markdown\n(.*?)\n```", note["content"], re.S)
    if not match:
        raise ValueError(f"the vault note {TEMPLATES[slot]!r} has no fenced template block")
    return match.group(1)


def _opts(raw: str) -> dict[str, str]:
    return dict(p.split("=", 1) for p in raw.strip("|").split("|") if "=" in p)


def _fmt(f: Fact) -> str:
    if f.text:
        return f.text
    v = f.value
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = num(v)                   # 3 significant figures; counts exact (BUG-059)
    return f"{v} {f.unit}".strip()


# Reader-facing text carries no developer codes (user decision 2026-10-27,
# BUG-058): no decision or bug numbers, no exception names, no file paths, no
# tool or flow identifiers. The facts sidecar keeps every reason as written.
# The four whole documents are thesis outputs that name their own parameters,
# and are left as they are.
DOCUMENT_BLOCKS = frozenset({"weather_report", "mcdm_decision_matrix", "mcdm_ranking",
                             "sensitivity_sweep"})
_PLAIN = [
    (re.compile(r"\s*\((?:D|BUG|Q|CP)-\d+[^()]*\)"), ""),
    (re.compile(r",\s*(?:D|BUG|Q|CP)-\d+\b"), ""),
    (re.compile(r"\s*\(HANDOVER [\d.]+\)"), ""),
    (re.compile(r"\s*\([A-Za-z]*(?:Error|Exception)\b[^()]*\)"), ""),
    (re.compile(r"\s*\(core/[\w/.]+\)"), ""),
    (re.compile(r"\bcore/report_config\.py\b"), "the event settings"),
    (re.compile(r",?\s*\bSOURCES\.md\b"), ""),
    (re.compile(r"\s*\(flow [a-z]+:[a-z_]+\)"), ""),
]


def _input_name(name: str) -> str:
    """A stored input's plain name: 'weather_grid' -> 'event weather'."""
    from . import workflows
    from .plain_language import workflow_name

    tool = workflows.tool_of(name)
    return workflow_name(tool)


def plain(text: str) -> str:
    from .plain_language import scrub, workflow_name

    if not text:
        return text
    out = text
    for pattern, repl in _PLAIN:
        out = pattern.sub(repl, out)
    out = re.sub(r"\(run ([a-z_]+)\)",
                 lambda m: f"(the {workflow_name(m[1])} step has not run)", out)
    out = re.sub(r"\bflow ([a-z]+):([a-z_]+)", lambda m: f"the {m[2].replace('_', ' ')} step", out)
    # "-- `weather_grid`" closing a How sure line names the data, not the store.
    out = re.sub(r" -- `([a-z_]+)`$", lambda m: f" -- {_input_name(m[1])}", out, flags=re.M)
    # A measurement printed as stored, with more decimals than its 3
    # significant figures (BUG-059): a value followed by its unit, by
    # "of the" / "of its", or by a bracket. Coordinates carry none of these
    # and keep their precision.
    out = re.sub(r"(?<![\w.,])-?\d+\.\d{3,}"
                 r"(?=\s?(?:m|km|km2|m2|mm|%|h)\b|\s+of\s+(?:the|its)\b|\s\()",
                 lambda m: num(float(m[0])), out)
    out = scrub(out)[0]
    out = re.sub(r"`([a-z]+(?:_[a-z0-9]+)+)`", lambda m: m[1].replace("_", " "), out)
    return out


def fill(kind: str, key: str, opts: dict[str, str], ctx: Ctx, llm: dict[str, str]) -> str:
    raw = _fill(kind, key, opts, ctx, llm)
    if kind == "llm" or key in DOCUMENT_BLOCKS:
        return raw
    if kind in ("figure", "map") and raw.startswith("!["):
        return raw                   # an image link; its note is made plain in _fill
    m = NK_MARK.match(raw)
    if m:                            # the marker is never made plain
        return m.group(0) + plain(raw[m.end():])
    return plain(raw)


def _fill(kind: str, key: str, opts: dict[str, str], ctx: Ctx, llm: dict[str, str]) -> str:
    if kind == "llm":
        limit = slot_limit(opts, getattr(ctx, "persona", ""))
        text = (llm or {}).get(key, "").strip()
        if not text:
            return f"_(to be written: {key}, at most {limit} words)_"
        return cut_words(shape_points(text), limit)
    f = ctx.facts[key]
    if not f.known:
        if kind in ("tile", "meter", "chip", "pictogram"):
            # Several sit on one line; a full reason each would bury the line.
            # The reason goes to the "Not known yet" list at the end instead.
            if f.gap == "data_source_needed":
                ctx.pending_reasons.setdefault(key, f"needs a data source for this country: "
                                                    f"{f.reason}")
                return f"*{label(key)}: needs a data source for this country*"
            ctx.pending_reasons.setdefault(key, f.reason)
            return f"*{label(key)}: not known yet*"
        if f.gap == "data_source_needed" and not f.text:
            return f"{NEEDS_SOURCE} {f.reason}"
        # A module block may say what is not known in its own words.
        return f.text or f"\x00nk:{key}\x00{NOT_KNOWN} {f.reason}"
    if kind in ("figure", "map"):
        if f.text:                   # a table in a figure slot renders as the table
            return f.text
        # An advisory map is a stand-in (the OSM overview for a flood overlay
        # that could not be drawn); its reason is printed under it.
        note = f"\n\n*{plain(f.reason)}*" if f.status == "advisory" and f.reason else ""
        rel = Path(f.value)
        try:
            rel = rel.resolve().relative_to(Path(ctx.store.dir).resolve())
        except ValueError:
            pass
        return f"![{key.replace('_', ' ')}]({rel.as_posix()})" + note
    if kind == "chip":
        return f"**[{f.value}]**"
    if kind == "meter":
        top = opts.get("max", "")
        if top == "100" and isinstance(f.value, (int, float)):
            return f"**{num(f.value)}%** {label(key)} ·"     # a share reads as % (BUG-059)
        if top and isinstance(f.value, (int, float)):
            return f"**{_fmt(Fact(key, f.value))}** / {top} {label(key)} ·"
        return f"**{_fmt(Fact(key, f.value))}** {f.unit or label(key)} ·"
    if kind == "tile":
        # A fact's unit reads as its label ("km2 flooded"); a bare value gets
        # the tile's label.
        return f"**{_fmt(Fact(key, f.value))}** {f.unit or label(key)} ·"
    if f.value == "" and not f.text:
        return EMPTY                 # a block with nothing to add; its line goes
    return _fmt(f)


PERSONAS = ("flood_bot", "chanakya", "norwood")


def slot_limit(opts: dict[str, str], persona: str) -> int:
    """An agent slot's word limit for the reader's persona: `|flood_bot=45`
    beside `|max_words=110` gives Flood bot its 2-3 lines and the other
    modes the fuller text (user decision 2026-11-02)."""
    return int(opts.get(persona) or opts.get("max_words", 60)) if persona in PERSONAS         else int(opts.get("max_words", 60))


# -- layout rules (user, 2026-11-18: "maintain stricter rules for new lines") ----
#
# Enforced here, in Python, whatever a model writes and whichever persona it
# writes for. The word cut used to split a slot into words and join them with
# single spaces -- every time, not only when over the limit -- so every list
# an agent wrote arrived as "1) ... 2) ..." in one paragraph.

_POINT = re.compile(r"(?:(?<=^)|(?<=\s))(\d{1,2})[.)]\s+(?=[A-Z*_\"'(\[])")


def _split_inline_list(par: str) -> str:
    """"Lead. 1) First. 2) Second." -> the lead, then one point per line.

    Only a run numbered 1, 2, 3 ... in order counts as a list, so "Risks 2
    and 3." or "INFORM 2.6" inside a sentence is never split.
    """
    marks, want = [], 1
    for m in _POINT.finditer(par):
        if int(m.group(1)) == want:
            marks.append(m)
            want += 1
    if len(marks) < 2:
        return par
    head = par[:marks[0].start()].rstrip()
    items = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(par)
        items.append(f"{m.group(1)}. {' '.join(par[m.end():end].split())}")
    return (head + "\n\n" if head else "") + "\n".join(items)


def shape_points(text: str) -> str:
    """An agent-written slot with one point per line and one blank line
    between paragraphs. Bullets and numbered points start their own line."""
    text = text.replace("\r\n", "\n").strip()
    pars = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    for par in pars:
        lines = [ln.strip() for ln in par.split("\n") if ln.strip()]
        is_list = all(re.match(r"(\d{1,2}[.)]|[-*])\s", ln) for ln in lines)
        if is_list and len(lines) > 1:
            out.append("\n".join(re.sub(r"^(\d{1,2})\)\s", r"\1. ", ln) for ln in lines))
        else:
            # Prose, or one line holding a whole run ("1) a 2) b 3) c"): one
            # paragraph, then any numbered run inside it split out.
            out.append(re.sub(r"^(\d{1,2})\)\s", r"\1. ", _split_inline_list(" ".join(lines))))
    # Points written one per paragraph are one list, not a loose one.
    merged: list[str] = []
    for par in out:
        point = all(re.match(r"(\d{1,2}\.|[-*])\s", ln) for ln in par.split("\n"))
        if merged and point and merged[-1].split("\n")[-1][:1] in "0123456789-*" and \
                re.match(r"(\d{1,2}\.|[-*])\s", merged[-1].split("\n")[-1]):
            merged[-1] += "\n" + par
        else:
            merged.append(par)
    return "\n\n".join(merged)


OVER_LIMIT = "_(Over its {limit}-word limit: {n} words.)_"


def cut_words(text: str, limit: int) -> str:
    """The text whole. Over `limit` words it is flagged, never cut (user,
    2026-11-22): the cut used to drop whole points behind a " ...", so a risk
    or an action could vanish unseen. Pipelines ask once for a shorter text
    first (core/pipelines/hev.py `_shorten`)."""
    n = len(text.split())
    if n <= limit:
        return text
    return text.rstrip() + "\n\n" + OVER_LIMIT.format(limit=limit, n=n)


def _merge_not_known(body: str) -> str:
    """One "Not known yet" line per reason per section.

    Module 9 printed the same "Not known yet -- GFM maps no flood ..." three
    times under Hazard: the map, the facility table and the food clusters
    each said it. Lines one reason produced are merged where the first stood:
    "Not known yet: the map, the facility table and the food clusters --
    <reason>". Exact repeats of any other line in a section are dropped too.
    """
    out_sections = []
    for section in re.split(r"(?m)^(?=## )", body):
        lines = section.split("\n")
        groups: dict[str, list[str]] = {}
        for ln in lines:
            m = NK_MARK.match(ln)
            if m:
                groups.setdefault(ln[m.end():], []).append(m.group(1))
        seen_nk, seen_text, out = set(), set(), []
        for ln in lines:
            m = NK_MARK.match(ln)
            if m:
                rest = ln[m.end():]
                if rest in seen_nk:
                    continue
                seen_nk.add(rest)
                keys = list(dict.fromkeys(groups[rest]))
                if len(keys) > 1:
                    names = [label(k) for k in keys]
                    named = ", ".join(names[:-1]) + " and " + names[-1]
                    reason = rest[len(NOT_KNOWN):].strip()
                    out.append(f"{NOT_KNOWN.rstrip(' -')}: {named} -- {reason}")
                else:
                    out.append(rest)
                continue
            if ln.strip() and not ln.startswith(("|", "#")) and ln in seen_text:
                continue
            if ln.strip():
                seen_text.add(ln)
            out.append(NK_MARK.sub("", ln))
        out_sections.append("\n".join(out))
    return "".join(s if s.endswith("\n") or not s else s + "\n" for s in out_sections).rstrip("\n")


def _tidy(body: str) -> str:
    """Exactly one blank line between blocks, none trailing a line, and a
    blank line before every heading and every list that follows prose."""
    body = "\n".join(ln.rstrip() for ln in body.split("\n"))
    body = re.sub(r"(?m)([^\n])\n(#{1,6} )", r"\1\n\n\2", body)
    body = re.sub(r"(?m)^((?!\d{1,2}\. |[-*] |\|).+)\n(\d{1,2}\. |[-*] )", r"\1\n\n\2", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip("\n")


def _close_empty(body: str) -> str:
    """Drop the lines of empty blocks, and the blank lines around each run of
    them down to what either side had -- so a block that says nothing leaves
    no gap, and every other blank line stays as written."""
    def keep(m: re.Match) -> str:
        return "\n" * max(len(m.group(1)), len(m.group(2)))

    return re.sub(r"(\n*)" + EMPTY + r"(?:\n*" + EMPTY + r")*(\n*)", keep, body).strip("\n")


def registry() -> dict[str, Callable[[Ctx], Fact]]:
    """The report facts and the module blocks: one namespace, so any template
    may place either."""
    from .module_blocks import BLOCKS

    clash = set(FACTS) & set(BLOCKS)
    if clash:
        raise ValueError(f"a module block and a report fact share a key: {sorted(clash)}")
    return {**FACTS, **BLOCKS}


def _compute(ctx: Ctx, wanted: set[str]) -> None:
    known = registry()
    for key in [k for k in known if k in wanted and k not in LAST] + [k for k in LAST if k in wanted]:
        try:
            ctx.facts[key] = known[key](ctx)
        except Exception as exc:                               # noqa: BLE001
            ctx.facts[key] = nk(key, f"could not be read ({type(exc).__name__}: {exc})"[:200])


def facts_sidecar(store: Any, path: Path, *, label: str,
                  pipeline_items: list[dict] | None = None) -> Path:
    """Every fact the renderer knows, computed from the store with no
    template -- the Flood analyst's facts layer (its outputs are Modules 1-10,
    which have no placeholders). The same functions as a report's sidecar, so
    a fact cannot be computed one way here and another way there."""
    ctx = Ctx(store)
    ctx.pipeline_items = list(pipeline_items or [])
    _compute(ctx, set(FACTS))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "report": label, "experiment": getattr(store, "slug", ""),
        "rendered": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "template": None, "inputs_used": list(ctx.inp.used),
        "facts": [asdict(f) for f in ctx.facts.values()], "llm_slots": [],
        "figures": ctx.figures, "unknown_placeholders": [], "details": ctx.details,
    }, indent=1, default=str), encoding="utf-8")
    return path


# The module each stored input belongs to, for grouping a report's dates.
_MODULE_OF_INPUT = {
    "data_availability": "data_availability", "availability": "data_availability",
    "weather_grid": "weather", "lulc": "land_use_pre_event", "worldcover": "land_use_pre_event",
    "terrain": "terrain", "permanent_water": "water_and_structures",
    "where_am_i": "water_and_structures", "rank_flood_products": "mcdm_ranking",
    "mcdm": "mcdm_ranking", "sensitivity_analysis": "sensitivity", "sensitivity": "sensitivity",
    "get_flood_map": "essential_services_hev", "get_flood_extent": "essential_services_hev",
    "flood_extent": "essential_services_hev", "calc_flood_depth": "essential_services_hev",
    "assess_road_access": "essential_services_hev",
    "check_slope_tree_roads": "essential_services_hev", "population": "essential_services_hev",
}
CONTEXT_GROUP = "National and historical context"
MODULE_ORDER: dict[str, int] = {}


def _module_of(name: str) -> str:
    from .experiment import BY_SLOT

    slot = _MODULE_OF_INPUT.get(name)
    if slot is None:
        return CONTEXT_GROUP
    entry = BY_SLOT[slot]
    label = f"Module {entry.n}, {entry.title}"
    MODULE_ORDER[label] = entry.n
    return label


def _data_dated(ctx: Ctx) -> Fact:
    """The acquisition lines of every input the report used, as one fact."""
    from . import dating

    zone = dating.store_zone(ctx.store)
    used = [(n, (ctx.store.cached_input(n) or {}).get("payload")) for n in ctx.inp.used]
    # One line per source, grouped by the module the data belongs to (BUG-060).
    groups: dict[str, list[str]] = {}
    for name, text in dating.merged(used, zone):
        groups.setdefault(_module_of(name), []).append(text)
    if not zone and groups:
        groups.setdefault(CONTEXT_GROUP, []).append(
            "The AOI's local time zone is not known for this event; times are UTC.")
    order = sorted(groups, key=lambda g: (g == CONTEXT_GROUP, MODULE_ORDER.get(g, 99)))
    md = [line for g in order for line in [f"- **{g}**", *[f"  - {t}" for t in groups[g]]]]
    fact = Fact("data_dated", [f"{g}: {t}" for g in order for t in groups[g]],
                source="core/dating.py, from the stored results' own time fields",
                text="\n".join(md))
    ctx.facts["data_dated"] = fact
    return fact


def render(store: Any, slot: str, llm: dict[str, str] | None = None,
           template_text: str | None = None,
           pipeline_items: list[dict] | None = None, persona: str = "") -> Path:
    """Render one module (1-12) into the store from its vault template, with
    its facts sidecar."""
    if slot not in TEMPLATES:
        raise KeyError(f"{slot!r} has no template; known: {sorted(TEMPLATES)}")
    if getattr(store, "registry_version", 2) != 2:
        raise ValueError(f"{getattr(store, 'slug', store)} is on output registry version "
                         f"{store.registry_version}; its outputs are kept as written")
    if slot in REPORTS:
        written = {s["slot"] for s in store.status() if s["written"]}
        missing = [s for s in REQUIRED if s not in written]
        if missing:
            raise ValueError(f"a {slot.replace('_', ' ')} needs the Flood analyst run of this AOI, "
                             f"window and cache policy first (D-24); not written yet: {missing}")
    text = template_text if template_text is not None else template(slot)
    ctx = Ctx(store)
    ctx.persona = persona
    ctx.pipeline_items = list(pipeline_items or [])
    wanted = {m.group(2) for m in PLACEHOLDER.finditer(text) if m.group(1) != "llm"}
    _compute(ctx, wanted)
    unknown = sorted(wanted - set(registry()))
    for key in unknown:
        ctx.facts[key] = nk(key, "the template names a fact the renderer does not know")
    body = PLACEHOLDER.sub(lambda m: fill(m.group(1), m.group(2), _opts(m.group(3)), ctx, llm or {}), text)
    # A trailing separator left by the last tile on a line is dropped, and
    # the blank lines an empty block leaves behind close up.
    body = "\n".join(line.rstrip(" ·") for line in body.splitlines())
    body = _close_empty(body)
    body = _tidy(_merge_not_known(body))
    if slot in REPORTS:
        # When the data the report quotes was acquired, with its zone (user
        # decision 2026-10-26). A fact too, so the lint traces its dates and
        # times to the sidecar and the agents writing the slots are handed
        # the same lines. A module places its own (`{{fact:data_acquired}}`).
        dated = _data_dated(ctx)
        if dated.value:
            body += "\n\n### When the data was acquired\n" + plain(dated.text)
    if ctx.pending_reasons:
        body += ("\n\n### Not known yet\n"
                 + "\n".join(f"- **{label(k)}**: {plain(r)}" for k, r in ctx.pending_reasons.items()))

    llm_slots = [{"slot": m.group(2), "max_words": slot_limit(_opts(m.group(3)), persona),
                  "status": "written" if (llm or {}).get(m.group(2)) else "pending"}
                 for m in PLACEHOLDER.finditer(text) if m.group(1) == "llm"]
    if slot in REPORTS:
        heading = _data_source(ctx)
    else:
        from .module_blocks import heading_source

        heading = heading_source(slot, ctx)
    path = store.write(slot, body, inputs_used=list(ctx.inp.used),
                       source="core/report_render.py", data_source=heading)
    sidecar = {"report": slot, "experiment": getattr(store, "slug", ""),
               "rendered": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "template": TEMPLATES[slot], "inputs_used": list(ctx.inp.used),
               # `text` is kept: a list or a table is a fact too, and the lint
               # traces every number in the report back to this file.
               "facts": [asdict(f) for f in ctx.facts.values()],
               "llm_slots": llm_slots,
               # The model's own words per written slot, kept apart from the
               # facts (RQ5: the interpretation layer). `written` is the reply
               # as parsed, `placed` what the report shows after the word cut.
               # Never read back into a prompt (_slots_task reads llm_slots).
               "llm_text": {s["slot"]: {"written": (llm or {})[s["slot"]].strip(),
                                        "placed": _fill("llm", s["slot"], _opts(m.group(3)), ctx, llm or {})}
                            for s, m in zip(llm_slots, [m for m in PLACEHOLDER.finditer(text)
                                                        if m.group(1) == "llm"])
                            if s["status"] == "written"},
               "figures": ctx.figures,
               "unknown_placeholders": unknown,
               "details": ctx.details}
    path.with_suffix(".facts.json").write_text(json.dumps(sidecar, indent=1, default=str),
                                               encoding="utf-8")
    from .charts import update_index

    for fig in ctx.figures:
        update_index(Path(store.dir) / "figures" / "index.json", fig)
    return path


def _data_source(ctx: Ctx) -> str:
    ext = ctx.get("extent")
    parts = [f"the {ext.get('label')} flood extent"] if ext else []
    if ctx.get("weather") is not None:
        parts.append("the event weather")
    if ctx.get("where") is not None:
        parts.append("OpenStreetMap")
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + (parts[-1] if parts else "")
