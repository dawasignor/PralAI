"""Flood analyst mode: Modules 1-10 in order.

    availability -> weather -> land cover (Sentinel-2, WorldCover only when
    Sentinel-2 failed its 0.7 threshold at the defaults, D-8) -> terrain ->
    permanent water and structures -> pre-ranking matrix -> ranking ->
    sensitivity -> the best-ranked product's flood extent -> population in it,
    INFORM, earlier disasters, the hazard-and-exposure map -> essential
    services (Module 9) -> human health risks (Module 10)

Accuracy runs only when the experiment's validation config exists, and is
stored, not rendered: no module carries it yet (validation is later).

The flood extent and the data of Modules 9 and 10 are fetched HERE
(core/pipelines/hev.py); the Flood bot and Chanakya pipelines read them back
from the store. Depth and road access are switched off (workflows.INACTIVE).
"""

from __future__ import annotations

from pathlib import Path

from ..outputs import Inputs, best_ranked, module_product
from .base import MissingInput, Pipeline, Run, agent, facts, render, tool

REPO = Path(__file__).resolve().parents[2]


def _dates(run: Run) -> dict:
    return {"bbox": run.bbox, "start_date": run.start, "end_date": run.end}


def _indices(run: Run) -> dict:
    """The five ranking inputs, the ranking's own way (mcdm.indices_from_payloads)."""
    from ..workflows.mcdm import indices_from_payloads

    weather, terrain = run.payload("weather_grid"), run.payload("terrain")
    lulc, worldcover = run.payload("lulc"), run.payload("worldcover")
    availability = run.payload("data_availability")
    missing = [n for n, p in (("weather", weather), ("terrain", terrain),
                              ("product availability", availability)) if p is None]
    if lulc is None and worldcover is None:
        missing.append("land cover")
    if missing:
        raise MissingInput(f"not stored: {', '.join(missing)}")
    out = indices_from_payloads(weather, lulc, terrain, availability=availability,
                                worldcover=worldcover,
                                lulc_failure=run.store.failure("lulc") if lulc is None else None)
    return dict(out["indices"])


def _s2_failed(run: Run) -> tuple[bool, str]:
    """The WorldCover fallback runs only on a recorded Sentinel-2 cloud failure
    at the defaults over this AOI (D-8). Never on a missing or other failure."""
    from ..workflows.mcdm import s2_failed_at_defaults

    if run.has("lulc"):
        return False, "Sentinel-2 land cover is stored; no fallback"
    ok, why = s2_failed_at_defaults(run.store.failure("lulc"), run.bbox)
    if ok:
        run.note("land_cover_fallback", f"WorldCover used for land cover: {why}")
        from ..logbook import event

        event("fallback-used", src="pipeline", pipeline=run.pipeline, step="fetch_worldcover")
    return ok, ("" if ok else f"no fallback: {why}")


def flood_map_params(run: Run) -> dict:
    """get_flood_map for the module product -- the user's choice, else the
    best-ranked (BUG-021; 2026-11-18): built from the run -- AOI, window, this
    run's JRC water -- so any AOI gets a flood map; no validation config is
    involved. A chosen product needs no ranking."""
    top, label, _ = module_product(run.store, Inputs(run.store))
    if top is None:
        raise MissingInput(label)
    water = (((run.payload("permanent_water") or {}).get("water") or {})
             .get("raster") or {}).get("path", "")
    # The area overview this run already fetched: the overlay draws its
    # buildings and places from it instead of asking OpenStreetMap again.
    where = Path(run.store.inputs_dir) / "where_am_i.json"
    return {"product": top, "bbox": run.bbox, "start": run.start, "end": run.end,
            "permanent_water_raster": water,
            "osm_context": str(where) if where.is_file() else "",
            "figure_dir": str(Path(run.store.dir) / "figures"), **run.out()}


def validation_config(run: Run) -> str:
    """The experiment's validation config, for accuracy_metrics only."""
    config = run.settings.get("flood_extent_config")
    if not config:
        raise MissingInput("no validation config is set for this experiment")
    return str(Path(config) if Path(config).is_absolute() else REPO / config)


def stored_extent(run: Run) -> dict | None:
    """The best-ranked product's stored flood map (`flood_map_<id>`, or an
    older `get_flood_map` / `get_flood_extent` record). Every later step --
    population, the hazard map, Modules 9-10 -- reads this one raster; the
    map is never fetched twice (user decision 2026-11-12)."""
    return Inputs(run.store).get("extent")


def mapped_extent(run: Run) -> dict:
    ext = stored_extent(run)
    if ext is None:
        raise MissingInput("the flood extent is not stored")
    if ext.get("status") == "no_flood_mapped":
        raise MissingInput(f"{ext.get('label')} maps no flood over the AOI in the window")
    if ext.get("status") != "flood_mapped":
        # Not known is not "no flood": the product could not be read.
        raise MissingInput(f"the {ext.get('label')} flood map is not known: "
                           f"{ext.get('reason') or 'it could not be evaluated'}")
    return ext


def observed_extent(run: Run) -> dict:
    """The stored extent when the product was READ -- flood mapped or not.

    For the steps that describe what the product saw rather than the flood
    itself: the hazard map and the population (Modules 9-10). A "no flood
    mapped" extent is still an observation map -- dry, permanent water and
    not observed are all in it -- and skipping those steps on it left the
    Kandy Module 9 with no map and no population (user bug, 2026-11-18).
    Depth and roads need a flood and keep `mapped_extent`. An extent that
    could not be read is still refused: not known is not "no flood".
    """
    ext = stored_extent(run)
    if ext is None:
        raise MissingInput("the flood extent is not stored")
    if ext.get("status") not in ("flood_mapped", "no_flood_mapped"):
        raise MissingInput(f"the {ext.get('label')} flood map is not known: "
                           f"{ext.get('reason') or 'it could not be evaluated'}")
    if not (ext.get("raster") or {}).get("path"):
        raise MissingInput(f"the {ext.get('label')} extent raster is not stored")
    return ext


def depth_params(run: Run) -> dict:
    return {"bbox": run.bbox, "extent_raster": mapped_extent(run)["raster"]["path"], **run.out()}


def roads_params(run: Run) -> dict:
    """The road check reads the depth when it is stored, for the SAME extent
    -- a depth computed from another product's extent is not this one's."""
    ext = mapped_extent(run)
    out = {"bbox": run.bbox, "extent_raster": ext["raster"]["path"]}
    dep = run.payload("calc_flood_depth")
    if dep and dep.get("extent_raster") == ext["raster"]["path"] and (dep.get("depth") or {}).get("raster"):
        out["depth_raster"] = dep["depth"]["raster"]["path"]
        prob = ((dep.get("error") or {}).get("rasters") or {}).get("band_change_prob_vehicle")
        if prob:
            out["band_change_raster"] = prob["path"]
    return out


def _accuracy(run: Run) -> tuple[bool, str]:
    config = run.settings.get("flood_extent_config")
    path = (REPO / config) if config and not Path(config).is_absolute() else Path(config or "")
    return (bool(config) and path.exists(),
            "no validation config for this experiment; accuracy is later")


def _brief_task(run: Run) -> str:
    """Norwood's brief AT THIS POINT of the graph: the land cover only (BUG-015).

    The step runs straight after the land cover is fetched, before terrain and
    permanent water exist -- it used to ask for all three, so every uncached
    run published a 'blocking failure' brief beside Module 3's own numbers.
    """
    from .. import dating

    zone = dating.store_zone(run.store)
    name = "lulc" if run.has("lulc") else "worldcover"
    dated = dating.lines(name, run.payload(name), zone)
    source = ("Sentinel-2 L2A" if name == "lulc" else
              "ESA WorldCover 2021 (the fallback: Sentinel-2 failed its threshold at the defaults)")
    return (f"Write the land cover brief for {run.experiment} from the stored {source} result: "
            "the scene and its cloud, vegetation, the built-up index and the other indices it "
            "computed. Terrain and permanent water are fetched in later steps -- they are not "
            "missing.\n" + "\n".join(f"dated: {t}" for t in dated))


def _has_land(run: Run) -> tuple[bool, str]:
    return (run.has("lulc") or run.has("worldcover"), "no land cover is stored")


def build() -> Pipeline:
    from . import hev

    return Pipeline("flood_analyst", "Flood analyst run", [
        tool("data_availability", lambda r: {**_dates(r), **r.out("figures")}),
        render("data_availability"),
        tool("fetch_weather", lambda r: {**_dates(r), **r.out("figures"), **r.params_for("fetch_weather")}),
        render("weather"),
        tool("fetch_lulc_data", lambda r: {**_dates(r), **r.out()}),
        tool("fetch_worldcover", lambda r: {"bbox": r.bbox, **r.out()}, when=_s2_failed),
        agent("norwood", "land_cover_brief", _brief_task, when=_has_land),
        render("land_use_pre_event"),
        tool("fetch_terrain_data", lambda r: {"bbox": r.bbox, **r.out()}),
        render("terrain"),
        tool("fetch_permanent_water", lambda r: {"bbox": r.bbox, **r.out()}),
        tool("where_am_i", lambda r: {"bbox": r.bbox, **r.out("figures")}),
        render("water_and_structures"),
        tool("rank_flood_products", _indices, max_s=120.0),
        render("mcdm_pre_ranking"),
        render("mcdm_ranking"),
        tool("sensitivity_analysis", lambda r: {**_indices(r), **r.out("figures")}, max_s=300.0),
        render("sensitivity"),
        # Modules 9 and 10 (user decision 2026-11-02): the data they rest on,
        # then each written by the remote sensing expert's hand-over and
        # Chanakya's sections (core/pipelines/hev.py).
        *hev.data_steps(),
        hev.module("essential_services_hev", "norwood"),
        hev.module("human_health_risks_hev", "norwood"),
        tool("accuracy_metrics", lambda r: {"config": validation_config(r), "aoi_bbox": r.bbox},
             when=_accuracy),
        facts("flood_analyst"),
    ])
