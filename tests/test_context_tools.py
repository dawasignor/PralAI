"""The CP4 context and impact tools, on canned replies and synthetic rasters.

    python tests/test_context_tools.py          # no model, no network, no cost
    python tests/test_context_tools.py --live   # also ask the real sources (free)

Held still here:

    1. where_am_i   -- houses and apartments ride in the building request;
                       government, industry and water facilities are unions of
                       selectors in the facilities request, and those are
                       LOCATED; a cut list says it was cut
    2. world bank   -- latest non-null year, national, NOT KNOWN when absent
    3. inform       -- the release used is the latest published BEFORE the
                       date asked for; climate change is read by country name
                       from a checked layout
    4. population   -- moved to tests/test_pop_data.py (WorldPop, 2026-10-30)
    5. disasters    -- nothing ending after the cutoff survives, and a source
                       not searched is never read as "nothing found"
    6. extent       -- no-data is lost coverage, not dry; a product with no
                       flood returns 0 with the reason
    7. roads        -- flooded km by class, bridges, hospital access at the
                       assumed distance; depth is never claimed
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import logbook, workflows                                  # noqa: E402
from core.workflows import (_fetch as F, context, disasters, impact,  # noqa: E402
                            osm_context)

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


class Canned:
    """Replace F.get_json with a URL-substring -> reply table."""

    def __init__(self, table: dict[str, object]):
        self.table, self.urls = table, []

    def __enter__(self):
        self.saved = F.get_json

        def fake(url, **_):
            self.urls.append(url)
            for key, reply in self.table.items():
                if key in url:
                    if isinstance(reply, Exception):
                        raise reply
                    return reply
            raise AssertionError(f"unexpected URL {url}")

        F.get_json = fake
        return self

    def __exit__(self, *exc):
        F.get_json = self.saved


def test_where_am_i_query() -> None:
    q = osm_context._count_query([79.9, 6.9, 80.0, 7.0], osm_context.COUNT_GROUPS["buildings"])
    check("houses and apartments ride in the building request",
          'name="houses"' in q and 'name="apartments"' in q and "out center" not in q)
    q = osm_context._count_query([79.9, 6.9, 80.0, 7.0], osm_context.COUNT_GROUPS["facilities"])
    check("government is a union of townhall, office and building tags",
          q.count('"office"="government"') == 3 and '"amenity"="townhall"' in q)
    check("drinking water and wastewater are separate, and a historic or ruined "
          "water feature is not a facility (BUG-038)",
          'name="water_facilities"' in q and 'name="wastewater_facilities"' in q
          and '[!"historic"][!"ruins"]' in q and '"man_made"="wastewater_plant"' in q
          and '"man_made"="pipeline"' not in q)
    check("facility categories are located with out center",
          q.count("out center tags") == len(osm_context.COUNT_GROUPS["facilities"]))
    items = osm_context._located([
        {"type": "count", "tags": {"total": "3"}},
        {"type": "node", "id": 1, "lat": 6.95, "lon": 79.95, "tags": {"name": "A", "amenity": "hospital"}},
        {"type": "way", "id": 2, "center": {"lat": 6.96, "lon": 79.96}, "tags": {"amenity": "clinic"}},
        {"type": "relation", "id": 3, "tags": {}}])
    check("a node and a polygon centre are located; an unlocatable one is skipped",
          [i["osm"] for i in items] == ["node/1", "way/2"] and items[0]["tags"]["amenity"] == "hospital")


def test_world_bank() -> None:
    reply = lambda v, y: [{"lastupdated": "2026-10-08"},  # noqa: E731
                          [{"country": {"value": "Sri Lanka"}, "date": y, "value": v}]]
    with Canned({"NY.GDP": reply(1.08e11, "2025"), "SP.POP": [{"lastupdated": "x"}, None]}):
        out = context.fetch_world_bank_indicators("lka")
    check("GDP is the latest year served, labelled national",
          out["indicators"]["gdp_usd"]["value"] == 1.08e11
          and out["indicators"]["gdp_usd"]["year"] == 2025 and out["scope"] == "national")
    check("a missing value is NOT KNOWN, never 0",
          out["indicators"]["population"]["value"] is None
          and "NOT KNOWN" in out["indicators"]["population"]["status"])
    try:
        context.fetch_world_bank_indicators("Sri Lanka")
        check("a non-ISO3 country is refused", False)
    except ValueError:
        check("a non-ISO3 country is refused", True)


def test_inform() -> None:
    wf = lambda i, n, pub: {"WorkflowId": i, "Name": n, "FlagGnaPublished": pub}  # noqa: E731
    table = {
        "GetByYear/2025": [wf(503, "INFORM Risk Mid 2025", "2025-08-28T00:00:00"),
                           wf(505, "INFORM Risk 2026", "2025-12-10T00:00:00")],
        "GetByYear/2024": [wf(480, "INFORM Risk Mid 2024", "2024-08-01T00:00:00")],
        "GetByYear/2023": ConnectionError("dropped"),
        "GetByYear/2022": [],
        "WorkflowId=503": [{"IndicatorId": "INFORM", "IndicatorScore": 3.3, "Unit": "0-10"},
                           {"IndicatorId": "HA", "IndicatorScore": 2.8},
                           {"IndicatorId": "VU", "IndicatorScore": 2.6},
                           {"IndicatorId": "CC", "IndicatorScore": 5.0}],
        "country/LKA?": [{}, [{"name": "Sri Lanka"}]],
    }
    saved = context._climate
    context._climate = lambda name: {"status": "ok", "risk_index": {"2050_pessimistic": 3.6},
                                     "name_seen": name}
    try:
        with Canned(table):
            out = context.fetch_inform_risk("LKA", before="2025-11-23")
    finally:
        context._climate = saved
    check("the release is the latest published BEFORE the date",
          out["release"]["workflow_id"] == 503, str(out["release"]))
    check("the four headline scores are read by indicator id",
          {k: v["value"] for k, v in out["scores"].items()}
          == {"inform_risk": 3.3, "hazard_exposure": 2.8, "vulnerability": 2.6,
              "lack_of_coping_capacity": 5.0})
    check("a year the API dropped is reported, not hidden",
          any(i["key"] == "unreadable_years" and "2023" in i["statement"]
              for i in out["uncertainty"]["tool"]))
    check("climate change is looked up by the World Bank country name",
          out["climate_change"].get("name_seen") == "Sri Lanka")


def test_disasters() -> None:
    feature = lambda name, start, end, lon: {  # noqa: E731
        "properties": {"name": name, "eventtype": "FL", "fromdate": start, "todate": end,
                       "alertlevel": "Green", "url": {"report": "https://www.gdacs.org/r"}},
        "geometry": {"coordinates": [lon, 6.94]}}
    table = {"country/LKA?": [{}, [{"name": "Sri Lanka"}]],
             "rapidmapping": {"results": [
                 {"code": "EMSR700", "name": "Flood in Sri Lanka", "countries": ["Sri Lanka"],
                  "eventTime": "2024-06-02T00:00:00", "centroid": "POINT (80.0 6.9)"},
                 {"code": "EMSR800", "name": "Flood in Sri Lanka", "countries": ["Sri Lanka"],
                  "eventTime": "2025-11-26T00:00:00", "centroid": "POINT (80.0 6.9)"},
                 {"code": "EMSR701", "name": "Flood in India", "countries": ["India"],
                  "eventTime": "2024-06-02T00:00:00"}]},
             "humdata": {"result": {"results": [
                 {"name": "unosat-lka", "title": "Satellite detected water extent in Gampaha",
                  "dataset_date": "[2024-06-04T00:00:00 TO 2024-06-04T23:59:59]",
                  "groups": [{"display_name": "Sri Lanka"}]}]}},
             "worldbank": {"documents": {
                 "D1": {"docdt": "2017-05-01T00:00:00Z", "docty": "Publication",
                        "display_title": "Sri Lanka - Rapid post disaster needs assessment : floods",
                        "url": "https://documents.worldbank.org/d1"},
                 "D2": {"docdt": "2018-01-01T00:00:00Z", "docty": "Procurement Plan",
                        "display_title": "Sri Lanka - Flood project procurement plan", "url": ""},
                 "facets": {}}},
             "gdacs.org": {"features": [feature("Old flood", "2024-06-01", "2024-06-05", 80.5),
                                        feature("The event", "2025-11-25", "2025-12-01", 80.0),
                                        feature("Straddling", "2025-11-20", "2025-11-26", 80.0)]}}
    with Canned(table) as canned:
        out = disasters.search_previous_disasters("LKA", before="2025-11-23",
                                                  bbox=[79.94, 6.905, 80.09, 6.978])
    gd = [e["name"] for e in out["events"] if e["source"] == "GDACS"]
    check("nothing ending after the cutoff survives -- the event cannot appear",
          gd == ["Old flood"] and out["dropped_at_or_after_cutoff"] == 2, str(gd))
    by = {e["source"]: e for e in out["events"]}
    check("Copernicus EMS: the country's activation before the cutoff only",
          [e["name"] for e in out["events"] if e["source"] == "Copernicus EMS"]
          == ["EMSR700 Flood in Sri Lanka"])
    check("UNOSAT and World Bank are searched; project paperwork is left out",
          "UNOSAT" in by and [e["name"] for e in out["events"] if e["source"] == "World Bank"]
          == ["Sri Lanka - Rapid post disaster needs assessment : floods"])
    check("every hit carries a short citation, publisher and year",
          by["World Bank"]["cite"] == "World Bank, 2017" and by["GDACS"]["cite"] == "GDACS, 2024"
          and by["UNOSAT"]["kind"] == "flood")
    check("each reference is listed once", len(out["references"]) == len(out["events"]))
    check("national news and government reports say not searched, never none",
          out["sources_status"]["national_news_and_government"]["status"] == "not_searched")
    check("every alert level is asked for", "alertlevel=Green%3BOrange%3BRed" in next(u for u in canned.urls if "gdacs" in u))
    check("the request stops at the cutoff", "toDate=2025-11-23" in next(u for u in canned.urls if "gdacs" in u))
    check("a hit carries its distance from the AOI", by["GDACS"]["km_from_aoi"] > 40
          and "km_from_aoi" not in by["World Bank"])
    check("ReliefWeb is not searched at all", not any("reliefweb" in u for u in canned.urls)
          and "reliefweb" not in out["sources_status"])
    try:
        disasters.search_previous_disasters("LKA", before="")
        check("a search with no cutoff is refused", False)
    except ValueError:
        check("a search with no cutoff is refused", True)


def _write(path: Path, arr: np.ndarray, *, nodata: int = 255) -> None:
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                       count=1, dtype="uint8", crs="EPSG:32644", nodata=nodata,
                       transform=from_origin(380000, 770000, 30, 30)) as dst:
        dst.write(arr, 1)


def test_extent(tmp: Path) -> None:
    from rasterio.warp import transform_bounds

    from core.workflows.validation import domain

    # A 100 x 100 product grid; western half flooded, a no-data strip, the rest dry.
    arr = np.zeros((100, 100), dtype="uint8")
    arr[:, :50] = 1
    arr[:25, :] = 255          # 750 m: reaches 450 m into the AOI (inset 300 m)
    _write(tmp / "prod.tif", arr)
    _write(tmp / "dry.tif", np.zeros((100, 100), dtype="uint8"))
    perm = np.zeros((100, 100), dtype="uint8")
    perm[50:60, :50] = 3
    _write(tmp / "perm.tif", perm, nodata=0)
    w, s, e, n = transform_bounds("EPSG:32644", "EPSG:4326", 380000 + 300, 767000 + 300,
                                  383000 - 300, 770000 - 300)
    # The loader checks the accuracy step's fields too; the extent reads none
    # of them, so they are stand-ins.
    cfg = {"event": "t", "aoi": {"bbox": [w, s, e, n]}, "seed": 1, "output_dir": ".",
           "reference": {"zip": "unused.zip", "flood_layer": "unused", "code": "T"},
           "grid": {"crs": "EPSG:32644", "resolution_m": 10},
           "evaluation": {"flood_fraction_threshold": 0.5, "domain_fraction_threshold": 0.5},
           "temporal": {"mode": "cumulative", "window": ["2025-11-24", "2025-12-01"]},
           "domain": {"exclude_wbm_classes": ["ocean"],
                      "permanent_water": {"raster": str(tmp / "perm.tif"), "classes": [3]}},
           "products": {p: {"label": p, "sensor": "SAR", "source": "files",
                            "encoding": {"flood": [1], "nonflood": [0], "nodata": [255]},
                            "files": [{"path": str(tmp / f), "datetime": "2025-11-26T00:00:00Z"}]}
                        for p, f in (("wet", "prod.tif"), ("dry", "dry.tif"))}}
    (tmp / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    saved = domain._sea
    domain._sea = lambda bbox, grid, exclude: np.zeros((grid["height"], grid["width"]), bool)
    try:
        wet = impact.get_flood_extent("wet", str(tmp / "config.yaml"), output_dir=str(tmp))
        dry = impact.get_flood_extent("dry", str(tmp / "config.yaml"), output_dir=str(tmp))
    finally:
        domain._sea = saved
    check("the flooded half is mapped, minus permanent water",
          wet["status"] == "flood_mapped" and 0.3 < wet["flood_fraction_of_observed"] < 0.5,
          str(wet.get("flood_fraction_of_observed")))
    check("no-data lowers coverage instead of counting as dry",
          0.80 < wet["coverage"] < 0.90, str(wet["coverage"]))
    check("a product with no flood returns 0 km2 with the reason, not an error",
          dry["status"] == "no_flood_mapped" and dry["flood_km2"] == 0.0
          and any(i["key"] == "no_flood" for i in dry["uncertainty"]["tool"]))
    check("the extent raster is written with its legend",
          Path(wet["raster"]["path"]).is_file() and wet["raster"]["legend"]["1"] == "flooded")
    test_roads(wet["raster"]["path"], [w, s, e, n])


def test_roads(raster: str, bbox: list[float]) -> None:
    w, s, e, n = bbox
    mid = (s + n) / 2
    west_lon, east_lon = w + 0.1 * (e - w), w + 0.9 * (e - w)
    roads = [{"id": 1, "class": "primary", "name": "Cross road", "bridge": False,
              "coords": [(west_lon, mid), (east_lon, mid)]},
             {"id": 2, "class": "tertiary", "name": "Bridge", "bridge": True,
              "coords": [(west_lon, mid + 0.002), (west_lon + 0.002, mid + 0.002)]}]
    hospitals = [{"name": "Near", "lon": west_lon, "lat": mid + 0.001, "osm": "node/9"},
                 {"name": "Far", "lon": east_lon, "lat": mid + 0.001, "osm": "node/8"}]
    saved = impact._roads_and_hospitals
    impact._roads_and_hospitals = lambda bbox: (roads, hospitals)
    try:
        out = impact.assess_road_access(bbox, raster)
    finally:
        impact._roads_and_hospitals = saved
    cross = out["roads_km_by_class"]["primary"]
    check("a road crossing the extent is split into flooded and dry km",
          cross["flooded_km"] > 0.5 and cross["dry_km"] > 0.5, str(cross))
    check("a bridge in the extent is listed, its deck height not claimed",
          out["bridges_in_flood_extent"] and "not known" in out["bridges_in_flood_extent"][0]["status"])
    by = {h["name"]: h for h in out["hospitals"]}
    check("hospital access is at risk only within the assumed distance",
          by["Near"]["access_at_risk"] and not by["Far"]["access_at_risk"])
    check("no passability is claimed without depth",
          out["road_state"] == impact.DEPTH_NOT_COMPUTED
          and any(i["key"] == "depth_not_computed" for i in out["uncertainty"]["tool"]))


def test_registry() -> None:
    names = set(workflows.names(include_inactive=True))
    want = {"fetch_world_bank_indicators", "fetch_inform_risk",
            "search_previous_disasters", "get_flood_extent", "assess_road_access",
            "check_slope_tree_roads", "where_am_i"}
    check("the CP4 tools are registered", want <= names, str(sorted(want - names)))
    # A helper slipped between a @workflow decorator and its function gets
    # registered under the tool's name: fetch_lulc_data was `step_down` from
    # 2026-10-27 to 2026-11-01, and every call failed its signature check.
    wrong = {n: workflows.REGISTRY[n].fn.__name__ for n in names
             if workflows.REGISTRY[n].fn.__name__ != n}
    check("every tool name is registered on the function of that name",
          not wrong, str(wrong))
    check("road access and the slope-and-tree check are switched off, not removed",
          {"assess_road_access", "check_slope_tree_roads"} <= set(workflows.INACTIVE)
          and not {"assess_road_access", "check_slope_tree_roads"} & set(workflows.names()))
    from core import prerequisites, report_render
    off = sorted(workflows.INACTIVE)
    check("a switched-off tool is refused by name, never run",
          all(workflows.resolve(n) == (None, "inactive") for n in off))
    check("... is never a valid next step",
          all(prerequisites.valid_workflow(n) == "" for n in off))
    check("... and the routing catalogue says NOT AVAILABLE",
          "NOT AVAILABLE" in workflows.catalogue().splitlines()[-1]
          and not any(ln.startswith(f"- {n}(") for n in off for ln in workflows.catalogue().splitlines()))
    nk = report_render.nk("depth_summary", "the flood depth is not computed", "calc_flood_depth")
    check("... and a NOT KNOWN fact does not offer it as a run",
          nk.run == [] and nk.needs == "" and "not available" in nk.reason, str(nk))
    check("fetch_place_context is retired for where_am_i",
          "fetch_place_context" not in names
          and workflows.RETIRED.get("fetch_place_context", ("",))[0] == "where_am_i")
    for name in want - {"where_am_i"}:
        _, notes = workflows.apply_contract({"uncertainty": F.blocks(), "sources": []}, {})
    check("their blocks are in the contract shape", notes == [])


def test_live() -> None:
    wb = context.fetch_world_bank_indicators("LKA")
    check("live: World Bank answers for Sri Lanka", wb["indicators"]["gdp_usd"]["value"] > 0)
    inf = context.fetch_inform_risk("LKA", before="2025-11-23")
    check("live: INFORM release before the event", inf["release"]["published"] < "2025-11-23",
          inf["release"]["name"])
    dis = disasters.search_previous_disasters("LKA", before="2025-11-23")
    check("live: GDACS answers, nothing after the cutoff",
          dis["sources_status"]["gdacs"]["status"] == "matched"
          and all((e["end"] or e["start"]) <= "2025-11-23" for e in dis["events"]))


def main() -> None:
    saved = logbook._log_path
    logbook._log_path = lambda: None
    try:
        test_where_am_i_query()
        test_world_bank()
        test_inform()
        test_disasters()
        with tempfile.TemporaryDirectory() as raw:
            test_extent(Path(raw))
        test_registry()
        if "--live" in sys.argv:
            test_live()
    finally:
        logbook._log_path = saved
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
