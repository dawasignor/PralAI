"""The data availability workflow: which NRT flood products saw the AOI, per day.

    python tests/test_data_availability.py          # canned catalogues, no network
    python tests/test_data_availability.py --live   # also search the real ones

Every catalogue call goes through `availability._request`, which the offline
tests replace with a router over canned STAC and CMR responses in the shapes
those services returned on 2026-10-11. What is held still: an acquisition is a
pass, not a file; days are local to the AOI; a day an NRT archive no longer
holds is `not_checkable`, never `none`; one failing catalogue never stops the
others; the cloud at the overpass is read, not invented; and the chart is drawn
and registered as a `run-` figure.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import figure_refs                                   # noqa: E402
from core.workflows import availability as av                  # noqa: E402
from core.workflows import weather_grid                        # noqa: E402
from core.workflows._mcdm_data import ALTERNATIVE_IDS          # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []

BBOX = [79.8, 6.783, 80.217, 7.083]


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


def item(item_id: str, when: str) -> dict:
    return {"id": item_id, "properties": {"datetime": when}}


# GFM: one pass on 26 Nov cut into two frames 25 s apart, an evening pass the
# same day, and a 1 Dec pass. 2 Dec is served on a SECOND page.
GFM_PAGE_1 = {"features": [
    item("ENSEMBLE_FLOOD_20251126T002515_VV_AS020M_E024N003T3", "2025-11-26T00:25:15Z"),
    item("ENSEMBLE_FLOOD_20251126T002540_VV_AS020M_E024N003T3", "2025-11-26T00:25:40Z"),
    item("ENSEMBLE_FLOOD_20251126T124916_VV_AS020M_E024N003T3", "2025-11-26T12:49:16Z"),
    item("ENSEMBLE_FLOOD_20251201T003327_VV_AS020M_E024N003T3", "2025-12-01T00:33:27Z")],
    "links": [{"rel": "next", "href": "https://stac.test/gfm-page-2", "method": "GET"}]}
GFM_PAGE_2 = {"features": [
    item("ENSEMBLE_FLOOD_20251202T002404_VV_AS020M_E024N003T3", "2025-12-02T00:24:04Z")],
    "links": []}
# DSWx-S1: one pass, two tiles, plus the same tile REPROCESSED under a new name.
DSWX_S1 = {"features": [
    item("OPERA_L3_DSWx-S1_T44NMN_20251126T002533Z_20251127T010000Z_S1A_30_v1.0", "2025-11-26T00:25:33Z"),
    item("OPERA_L3_DSWx-S1_T44NLN_20251126T002545Z_20251127T010000Z_S1A_30_v1.0", "2025-11-26T00:25:45Z"),
    item("OPERA_L3_DSWx-S1_T44NMN_20251126T002533Z_20260412T153226Z_S1A_30_v1.0", "2025-11-26T00:25:33Z")],
    "links": [{"rel": "next", "href": "https://cmr.test/next", "method": "POST",
               "body": {"token": "x"}}]}
# A pass at 20:00 UTC on 25 Nov is 01:30 on 26 Nov in Colombo: a local day.
DSWX_HLS = {"features": [
    item("OPERA_L3_DSWx-HLS_T44NMN_20251125T200000Z_20251126T000000Z_S2B_30_v1.1",
         "2025-11-25T20:00:00Z")]}


def router(fail: set[str] | None = None, offset_ok: bool = True):
    fail = fail or set()
    seen: list[tuple[str, dict | None]] = []

    def request(url: str, body: dict | None = None) -> dict:
        seen.append((url, body))
        if "forecast" in url:
            if not offset_ok:
                raise OSError("no route")
            return {"utc_offset_seconds": 19800, "timezone": "Asia/Colombo"}
        if "granules.json" in url:
            # The LANCE archive's oldest held granule: well after the event.
            return {"feed": {"entry": [{"time_start": "2026-10-03T00:00:00.000Z"}]}}
        if "/collections/" in url:
            starts = {"GFM": "2015-01-01T00:00:00Z", "S1_V1": "2023-12-01T00:00:00Z",
                      "VCDWD": "2025-11-26T00:00:00Z"}
            first = next((v for k, v in starts.items() if k in url), "2016-01-01T00:00:00Z")
            return {"extent": {"temporal": {"interval": [[first, None]]}}}
        if url == "https://stac.test/gfm-page-2":
            return GFM_PAGE_2
        if url == "https://cmr.test/next":
            return {"features": [], "links": []}
        collection = (body or {}).get("collections", [""])[0]
        if any(f in collection for f in fail):
            raise TimeoutError(f"{collection} timed out")
        if collection == "GFM":
            return GFM_PAGE_1
        if "DSWX-S1" in collection:
            return DSWX_S1
        if "DSWX-HLS" in collection:
            return DSWX_HLS
        return {"features": [], "links": []}

    request.seen = seen
    return request


def fake_weather(**kw) -> dict:
    """Hourly AOI-mean cloud: 100% on 26 Nov, 20% otherwise, one hour missing."""
    start = date.fromisoformat(kw["start_date"])
    end = date.fromisoformat(kw["end_date"])
    times, mean = [], []
    t = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    while t.date() <= end:
        times.append(t.strftime("%Y-%m-%dT%H:%MZ"))
        mean.append(100.0 if t.date() == date(2025, 11, 26) else 20.0)
        t += timedelta(hours=1)
    gap = times.index("2025-11-27T05:00Z")
    mean[gap] = None
    return {"hourly": {"time": times, "vars": {"cloud_cover_model": {"mean": mean}}},
            "meta": {"source": {"model": "ecmwf_ifs -- test", "retrieved_at": "now"}}}


def run(request, **kw) -> dict:
    """GIBS is unreachable unless `gibs_caps` (capabilities XML) is given."""
    from core.workflows import gibs

    original_req, original_wx, original_gibs = av._request, weather_grid.fetch_weather, gibs._get
    av._request = request
    weather_grid.fetch_weather = kw.pop("weather", fake_weather)
    caps = kw.pop("gibs_caps", None)

    def gibs_get(url: str) -> bytes:
        if caps is None:
            raise RuntimeError("offline test: GIBS not reachable")
        return caps.encode()

    gibs._get = gibs_get
    try:
        return av.data_availability(bbox=BBOX, start_date="2025-11-24",
                                    end_date="2025-12-02", **kw)
    finally:
        av._request, weather_grid.fetch_weather, gibs._get = original_req, original_wx, original_gibs


def by_id(payload: dict) -> dict[str, dict]:
    return {p["id"]: p for p in payload["products"]}


def test_counting(tmp: Path) -> None:
    payload = run(router(), output_dir=str(tmp / "outputs"), event_id="kelani")
    p = by_id(payload)
    check("the five products are the MCDM's five, in its order",
          tuple(x["id"] for x in payload["products"]) == ALTERNATIVE_IDS)
    check("days are local: civil UTC offset from the zone lookup",
          payload["period"]["utc_offset_h"] == 5.5
          and payload["period"]["timezone"] == "Asia/Colombo")

    gfm = p["gfm"]["days"]
    check("two frames of one pass are ONE acquisition; the evening pass is another",
          gfm["2025-11-26"]["count"] == 2
          and gfm["2025-11-26"]["acquisitions"][0]["files"] == 2,
          str(gfm["2025-11-26"]))
    check("a second STAC page is followed",
          gfm["2025-12-02"]["status"] == "available", str(gfm["2025-12-02"]["status"]))
    check("searched and empty is 'none' with count 0",
          gfm["2025-11-27"] ["status"] == "none" and gfm["2025-11-27"]["count"] == 0)

    s1 = p["dswx_s1"]["days"]["2025-11-26"]
    check("two tiles and a reprocessed copy of one pass count once",
          s1["count"] == 1 and s1["acquisitions"][0]["tiles"] == ["44NLN", "44NMN"]
          and s1["acquisitions"][0]["files"] == 3, str(s1))
    hls = p["dswx_hls"]["days"]
    check("a 20:00 UTC pass lands on the NEXT local day (Colombo +5:30)",
          hls["2025-11-26"]["count"] == 1 and hls["2025-11-25"]["count"] == 0)

    mcdwd = p["mcdwd"]
    check("MODIS days the rolling archive no longer holds are not_checkable, never none",
          all(d["status"] == "not_checkable" for d in mcdwd["days"].values()))
    check("and say the product is produced daily",
          "produced daily" in mcdwd.get("note", ""))
    vcdwd = p["vcdwd"]["days"]
    check("a day before the product existed is not_produced; after it, not_checkable",
          vcdwd["2025-11-25"]["status"] == "not_produced"
          and vcdwd["2025-11-26"]["status"] == "not_checkable",
          f"{vcdwd['2025-11-25']['status']} / {vcdwd['2025-11-26']['status']}")

    check("an unreachable GIBS changes nothing and says so",
          payload["gibs"]["checked"] is False and "unreachable" in payload["gibs"]["why"])

    # BUG-030 (user decision 2026-11-12): a day LANCE no longer holds but the
    # GIBS archive does is READ there and counted like the other products --
    # 1 = the day's composite -- never shown as 0 passes.
    import numpy as np
    from core.workflows import gibs

    caps = ("<Layer><ows:Identifier>MODIS_Combined_Flood_1-Day</ows:Identifier>"
            "<Value>2025-11-20/2025-11-25/P1D</Value><Value>2025-11-28/2026-10-26/P1D</Value>"
            "</Layer>")
    reads = []

    def fake_fetch(layer, day, bbox, tm, rgb2val):
        reads.append((layer, day.isoformat()))
        arr = np.zeros((4, 4), "uint8")                    # observed, no water
        problems = []
        if day.isoformat() == "2025-11-24":
            arr[:] = 255                                   # held, all cloud
        elif day.isoformat() == "2025-11-28":
            arr[0, :] = 3                                  # held, a flood row
        elif day.isoformat() == "2025-11-29":
            arr[:] = gibs.NO_TILE                          # GIBS has no tile
            problems = ["tile 1/2: HTTP Error 404: Not Found"]
        elif day.isoformat() == "2025-11-30":
            arr[:] = gibs.NO_TILE                          # the read failed
            problems = ["tile 1/2: <urlopen error timed out>"]
        return arr, None, problems

    saved = gibs.finest_matrix, gibs.colormap, gibs.fetch
    gibs.finest_matrix, gibs.colormap, gibs.fetch = (lambda caps: {}), (lambda: {}), fake_fetch
    try:
        held = by_id(run(router(), gibs_caps=caps, write_figure=False))
    finally:
        gibs.finest_matrix, gibs.colormap, gibs.fetch = saved
    days = held["mcdwd"]["days"]
    status = {k: (v["status"], v["count"]) for k, v in days.items()}
    check("a GIBS day whose composite saw ground is available, counted 1 (one daily composite)",
          status["2025-11-25"] == ("available", 1) and status["2025-11-28"] == ("available", 1)
          and days["2025-11-28"]["source"] == "gibs"
          and days["2025-11-28"]["acquisitions"][0]["composite"] == "1-Day", str(status))
    check("... a composite that saw only cloud is 0, marked all_cloud (user, 2026-11-12)",
          status["2025-11-24"] == ("none", 0) and days["2025-11-24"].get("all_cloud") is True
          and days["2025-11-24"]["observed_fraction"] == 0.0
          and days["2025-11-28"]["acquisitions"][0]["observed_fraction"] == 1.0
          and not days["2025-11-29"].get("all_cloud"), str(days["2025-11-24"]))
    check("a GIBS day with no product over the AOI is none (0); a failed read is unreachable",
          status["2025-11-29"] == ("none", 0) and status["2025-11-30"] == ("unreachable", 0),
          str(status))
    check("a day outside GIBS's ranges is not_checkable (no data), and is not read",
          status["2025-11-26"][0] == "not_checkable" and status["2025-11-27"][0] == "not_checkable"
          and ("MODIS_Combined_Flood_1-Day", "2025-11-26") not in reads, str(reads))
    check("totals count the GIBS days like any other pass",
          held["mcdwd"]["totals"]["instances"] == 4 and held["mcdwd"]["totals"]["days_available"] == 4
          and held["mcdwd"]["totals"]["days_in_gibs"] == 7
          and held["mcdwd"]["totals"]["days_all_cloud"] == 1
          and "daily composite" in held["mcdwd"].get("gibs_note", ""),
          str(held["mcdwd"]["totals"]))

    cloud = mcdwd["days"]["2025-11-26"]["cloud_pct"]
    check("cloud at both MODIS overpasses is read from the weather grid",
          cloud == {"Terra": 100.0, "Aqua": 100.0}, str(cloud))
    check("VIIRS gets its own overpass only",
          set(p["vcdwd"]["days"]["2025-11-26"]["cloud_pct"]) == {"VIIRS"})
    check("a missing hour gives None, never a filled value",
          p["mcdwd"]["days"]["2025-11-27"]["cloud_pct"]["Terra"] is None,
          str(p["mcdwd"]["days"]["2025-11-27"]["cloud_pct"]))
    check("the SAR products carry no cloud", "cloud_pct" not in gfm["2025-11-26"])
    check("the cloud source is named", payload["cloud"].get("model") == "ecmwf_ifs -- test")

    check("totals add up",
          p["gfm"]["totals"]["instances"] == 4 and p["gfm"]["totals"]["days_available"] == 3,
          str(p["gfm"]["totals"]))
    check("sources are listed", len(payload["sources"]) >= 4
          and any("stac.eodc.eu" in s["url"] for s in payload["sources"]))

    png = Path(payload["figure"]["path"])
    check("the chart is drawn", png.is_file() and png.read_bytes()[:4] == b"\x89PNG", str(png))
    return payload


def test_registration(tmp: Path, payload: dict) -> None:
    outputs = tmp / "outputs"
    index = outputs / "run_figures.json"
    entries = figure_refs.register_run({"result_1": {"output": payload}}, "kelani-2025",
                                       outputs, index)
    names = [e["name"] for e in entries]
    check("the chart is registered as a run- figure",
          names == ["run-kelani-2025-data_availability"], str(names))
    jpg, _ = figure_refs.resolve_run("run-kelani-2025-data_availability", outputs, index)
    check("and served as a JPEG", jpg.read_bytes()[:3] == b"\xff\xd8\xff")


def test_failures(tmp: Path) -> None:
    payload = run(router(fail={"DSWX-HLS"}, offset_ok=False),
                  output_dir=str(tmp / "outputs"), write_figure=False)
    p = by_id(payload)
    check("one failing catalogue is reported, the others still answer",
          p["dswx_hls"]["status"] == "unreachable" and "TimeoutError" in p["dswx_hls"]["note"]
          and p["gfm"]["status"] == "searched")
    check("its days are 'unreachable', not 'none'",
          all(d["status"] == "unreachable" for d in p["dswx_hls"]["days"].values()))
    check("a failed zone lookup falls back to solar time, and says so",
          payload["period"]["offset_source"].startswith("solar"))

    def broken(**kw):
        raise OSError("Open-Meteo down")
    payload = run(router(), write_figure=False, weather=broken)
    check("no weather: cloud is reported unavailable, and every value is None",
          "unavailable" in payload["cloud"]
          and by_id(payload)["mcdwd"]["days"]["2025-11-26"]["cloud_pct"]["Terra"] is None)

    for bad, message in ((dict(bbox=[80, 7, 79, 6]), "west<east"),
                         (dict(start_date="2025-12-02", end_date="2025-11-01"), "before"),
                         (dict(start_date="2025-01-01", end_date="2025-06-01"), "at most")):
        args = {"bbox": BBOX, "start_date": "2025-11-24", "end_date": "2025-12-02", **bad}
        try:
            av.data_availability(**args, write_figure=False, include_cloud=False)
            check(f"refused: {message}", False, "accepted")
        except ValueError as exc:
            check(f"refused: {message}", message in str(exc), str(exc)[:70])


def test_live() -> None:
    payload = av.data_availability(bbox=BBOX, start_date="2025-11-20",
                                   end_date="2025-12-05", write_figure=False)
    for product in payload["products"]:
        ok = product["status"] == "searched"
        results.append((PASS if ok else SKIP, f"live {product['id']}", str(product["totals"])))
        print(f"[{PASS if ok else SKIP}] live {product['id']}: {product['totals']}")


def test_event_date_offset() -> None:
    """BUG-017: the lookup answers with TODAY's offset (London +0 after the
    clocks went back on 2026-10-25); an August window must be dated UTC+1, and
    a window across the October change dates each side with its own offset."""
    def london(url: str, body: dict | None = None) -> dict:
        if "forecast" in url:
            return {"utc_offset_seconds": 0, "timezone": "Europe/London"}
        if "/collections/" in url:
            return {"extent": {"temporal": {"interval": [["2015-01-01T00:00:00Z", None]]}}}
        if "granules.json" in url:
            return {"feed": {"entry": [{"time_start": "2015-01-01T00:00:00.000Z"}]}}
        if (body or {}).get("collections", [""])[0] == "GFM":
            return {"features": [item("A_20260814T062905", "2026-08-14T06:29:05Z"),
                                 item("B_20261026T062905", "2026-10-26T06:29:05Z")],
                    "links": []}
        return {"features": [], "links": []}

    original_req = av._request
    av._request = london
    try:
        aug = av.data_availability(bbox=[-3.25, 55.9, -3.1, 56.0], start_date="2026-08-14",
                                   end_date="2026-08-23", write_figure=False,
                                   include_cloud=False)
        oct_ = av.data_availability(bbox=[-3.25, 55.9, -3.1, 56.0], start_date="2026-10-20",
                                    end_date="2026-10-28", write_figure=False,
                                    include_cloud=False)
    finally:
        av._request = original_req
    check("an August window over London is UTC+1, not today's +0",
          aug["period"]["utc_offset_h"] == 1.0, str(aug["period"].get("utc_offset_h")))
    first = by_id(aug)["gfm"]["days"]["2026-08-14"]["acquisitions"][0]
    check("an August pass is dated in BST", first["local_time"] == "07:29", first["local_time"])
    after = by_id(oct_)["gfm"]["days"]["2026-10-26"]["acquisitions"][0]
    check("a window across the clock change dates each pass with its own offset",
          after["local_time"] == "06:29" and "clock_change" in oct_["period"],
          f"{after['local_time']} / {oct_['period'].get('clock_change', 'no note')}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        payload = test_counting(tmp_path)
        test_registration(tmp_path, payload)
        test_failures(tmp_path)
        test_event_date_offset()
    if "--live" in sys.argv:
        test_live()
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{sum(r[0] == PASS for r in results)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
