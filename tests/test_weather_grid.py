#!/usr/bin/env python
"""fetch_weather: the model is named, and the label says which one.

    python tests/test_weather_grid.py           # offline, fake endpoint
    python tests/test_weather_grid.py --live    # also re-fetch Kelani

Until 2026-09-26 the workflow sent no `models` parameter, took Open-Meteo's
`best_match`, and labelled every payload "ERA5, 31 km". best_match was ECMWF
IFS 9 km in 192 of 192 hours over Kelani. These checks keep the request, the
label and the warnings tied to one named model.

    1. request   -- the model is in the URL, default and explicit
    2. label     -- meta.source is written from the requested model
    3. refusals  -- best_match, an unknown model, IFS before 2017
    4. spacing   -- the default and the oversampling warning follow the model
    5. forecast  -- hours after retrieval are flagged as forecast
    6. gaps      -- a missing hour is audited and withheld, never counted as 0
    7. refusal   -- a 400 carries Open-Meteo's reason, not "Bad Request"
    8. catalogue -- the registered description names every accepted model
    9. one tool  -- fetch_weather is the one weather tool:
                    no soil moisture requested, the old name followed with a
                    renamed-tool line, the point tool retired, the cache name
                    kept as weather_grid
   10. live      -- (--live) the cached Kelani grid is reproduced by ecmwf_ifs,
                    and the gap-aware summary equals the old one on it
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.workflows import weather_grid as wg  # noqa: E402

BBOX = [100.33, 13.22, 100.94, 13.96]           # Bangkok, roughly


def hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def fail(msg: str):
    raise SystemExit(f"\n  FAIL: {msg}")


class FakeArchive:
    """Stands in for `_get`: records each URL and answers with flat series.

    `holes` maps an hour index (from the start of the fetch) to the grid-point
    indices that return null at that hour, or to None for every point.
    """

    def __init__(self):
        self.urls: list[str] = []
        self.holes: dict[int, list[int] | None] = {}

    def query(self, n: int = -1) -> dict[str, str]:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.urls[n]).query))

    def __call__(self, url: str):
        self.urls.append(url)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        n = len(q["latitude"].split(","))
        start = datetime.fromisoformat(q["start_date"])
        end = datetime.fromisoformat(q["end_date"]) + timedelta(hours=23)
        hours = int((end - start).total_seconds() // 3600) + 1
        times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
                 for h in range(hours)]
        block = {"elevation": 5.0,
                 "hourly": {"time": times,
                            **{name: [1.0] * hours for name in wg.SPEC}}}
        blocks = [json.loads(json.dumps(block)) for _ in range(n)]
        for hour, which in self.holes.items():
            for p in (range(n) if which is None else which):
                for name in wg.SPEC:
                    blocks[p]["hourly"][name][hour] = None
        return blocks, "0" * 64, 1


def _run(fake: FakeArchive, **kwargs):
    params = {"bbox": BBOX, "start_date": "2026-09-01",
              "end_date": "2026-09-03", "antecedent_days": 2}
    params.update(kwargs)
    return wg.fetch_weather(**params)


def check_request(fake):
    _run(fake)
    if fake.query().get("models") != wg.DEFAULT_MODEL:
        fail(f"default call did not name the model: {fake.query()}")
    _run(fake, model="era5")
    if fake.query().get("models") != "era5":
        fail(f"explicit era5 not sent: {fake.query().get('models')}")
    print(f"  default sends models={wg.DEFAULT_MODEL}; model='era5' sends era5")
    print("\n  OK: every request names its model.")


def check_label(fake):
    for model, res in (("ecmwf_ifs", 9), ("era5", 31)):
        src = _run(fake, model=model)["meta"]["source"]
        if src["model_id"] != model or not src["model"].startswith(model):
            fail(f"{model}: label {src['model']!r} / id {src['model_id']!r}")
        if src["native_res_km"] != res:
            fail(f"{model}: native_res_km {src['native_res_km']}, want {res}")
        if "best_match" in src["model"] or ("ERA5" in src["model"]) != (model == "era5"):
            fail(f"{model}: label names the wrong model: {src['model']!r}")
        print(f"  {model:9s} -> {src['model']!r}, {src['native_res_km']} km")
    print("\n  OK: the label is the requested model.")


def check_refusals(fake):
    before = len(fake.urls)
    for bad in ("best_match", "era5_land", "ERA5"):
        try:
            _run(fake, model=bad)
        except ValueError as exc:
            print(f"  {bad!r:12s} refused: {str(exc)[:70]}...")
        else:
            fail(f"model {bad!r} was accepted")
    # The antecedent days count: a January 2017 window reaches back into 2016.
    try:
        _run(fake, start_date="2017-01-10", end_date="2017-01-12",
             antecedent_days=30)
    except ValueError as exc:
        print(f"  IFS reaching 2016 refused: {str(exc)[:70]}...")
    else:
        fail("an IFS fetch starting in 2016 was accepted")
    if len(fake.urls) != before:
        fail("a refused call still reached the endpoint")
    _run(fake, model="era5", start_date="2015-11-01", end_date="2015-11-03")
    print("  era5 for 2015 accepted")
    print("\n  OK: refused before any request is made.")


def check_spacing(fake):
    for model, want in (("ecmwf_ifs", 0.1), ("era5", 0.25)):
        out = _run(fake, model=model)
        if out["grid"]["spacing_deg"] != want:
            fail(f"{model}: default spacing {out['grid']['spacing_deg']}, want {want}")
        print(f"  {model:9s} default spacing {want} deg, "
              f"{out['grid']['n_points']} points")

    def oversampled(out):
        return any("finer than" in w for w in out["qc"]["warnings"])

    if oversampled(_run(fake, model="ecmwf_ifs", spacing_deg=0.1)):
        fail("0.1 deg on the 9 km IFS grid was flagged as oversampling")
    if not oversampled(_run(fake, model="era5", spacing_deg=0.1)):
        fail("0.1 deg on the 0.25 deg ERA5 grid was not flagged")
    print("  0.1 deg: no warning on IFS, oversampling warning on ERA5")
    print("\n  OK: spacing follows the model.")


def check_forecast(fake):
    def ahead(out):
        return [w for w in out["qc"]["warnings"] if "FORECAST" in w]

    if ahead(_run(fake)):
        fail("a window in the past was flagged as forecast")
    today = datetime.now(timezone.utc).date()
    # The tool's clock is frozen INSIDE the hours the archive returns: noon of
    # the window's last day (BUG-007). On the wall clock, between 23:00 and
    # 24:00 UTC the retrieval time is past the last returned stamp (23:00), no
    # hour is ahead of it, and this check failed once a day for that reason.
    frozen = datetime(today.year, today.month, today.day, 12, tzinfo=timezone.utc)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz is None else frozen.astimezone(tz)

    real, wg.datetime = wg.datetime, _Clock
    try:
        warned = ahead(_run(fake, start_date=(today - timedelta(days=1)).isoformat(),
                            end_date=today.isoformat()))
    finally:
        wg.datetime = real
    if not warned:
        fail("a window ending today was not flagged as forecast")
    print(f"  window ending today: {warned[0][:78]}...")
    print("\n  OK: hours after retrieval are marked forecast.")


def check_gaps(fake):
    """A missing hour is reported and withheld, never counted as 0 mm.

    Until 2026-09-26 the storm series was built with `_mean(row) or 0.0`, so a
    missing hour entered every total as a dry one. The window here is 72 h of
    1 mm/h everywhere; three event hours are knocked out -- two at every grid
    point, one at a single point -- plus one antecedent hour.
    """
    from core.reports import weather_markdown

    # Complete first: the audit is present and says so.
    fake.holes = {}
    out = _run(fake)
    gaps = out["qc"]["gaps"]
    if not gaps["window_complete"] or gaps["by_var"] or gaps["withheld"]:
        fail(f"a complete fetch must audit as complete: {gaps}")
    if out["event_summary"]["storm_total_mm"]["aoi_mean"] != 72.0:
        fail(f"72 h of 1 mm/h should total 72.0: {out['event_summary']['storm_total_mm']}")
    if "**Complete.**" not in weather_markdown(out):
        fail("the report must say a complete payload is complete")
    print("  complete : audit empty, storm total 72.0 mm, report says Complete")

    # Fetch starts 2 antecedent days early, so the event begins at hour 48.
    fake.holes = {48 + 10: None, 48 + 11: None, 48 + 30: [0], 5: None}
    out = _run(fake)
    s, gaps = out["event_summary"], out["qc"]["gaps"]
    precip = gaps["by_var"].get("precipitation") or {}
    ev = precip.get("event") or {}
    if (ev.get("hours_missing"), ev.get("partial_hours")) != (3, 1):
        fail(f"expected 3 missing event hours, 1 of them partial: {ev}")
    if [r[2] for r in ev.get("missing_utc", [])] != [2, 1]:
        fail(f"missing hours should read as two runs, 2 h and 1 h: {ev}")
    if precip["antecedent"]["hours_missing"] != 1 or gaps["antecedent_complete"]:
        fail(f"the antecedent hole must be audited: {precip['antecedent']}")
    print(f"  audit    : {ev['hours_missing']} event hours missing "
          f"({ev['partial_hours']} partial), runs {ev['missing_utc']}")

    mean = out["hourly"]["vars"]["precipitation"]["mean"]
    if mean[10] is not None or mean[11] is not None or mean[30] is not None:
        fail(f"gap hours must be None, never a number: {mean[9:13]}, {mean[30]}")
    if 0.0 in mean:
        fail("a gap was turned into 0 mm somewhere in the hourly mean")
    print("  hourly   : the three gap hours are None, a partial hour included")

    tot = s["storm_total_mm"]
    if tot["aoi_mean"] is not None or tot["max_point"] is not None:
        fail(f"the storm total must be withheld across a gap: {tot}")
    if tot["observed_hours_lower_bound_mm"] != 69.0 or tot["observed_hours"] != 69:
        fail(f"the floor should be the 69 observed hours, 69.0 mm: {tot}")
    must = {"event_summary.storm_total_mm.aoi_mean",
            "event_summary.max_intensity_mm.24h",
            "event_summary.antecedent_api_mm.d5",
            "event_summary.max_gust_ms",
            "qc.spatial_heterogeneity.precipitation_cv"}
    if must - set(gaps["withheld"]):
        fail(f"not withheld: {sorted(must - set(gaps['withheld']))}")
    if any(b.get("touches_gap") is None for b in s["rain_bursts"]):
        fail(f"every burst here touches a gap and must say so: {s['rain_bursts']}")
    print(f"  summary  : storm total withheld, floor {tot['observed_hours_lower_bound_mm']} mm "
          f"over {tot['observed_hours']} h; {len(gaps['withheld'])} figures withheld")

    doc = weather_markdown(out)
    for needle in ("## Uncertainty: data completeness", "**Incomplete.**",
                   "*withheld (gaps)*", "a floor, not the total", "| missing h |"):
        if needle not in doc:
            fail(f"the report is missing {needle!r}")
    if doc.index("## Uncertainty: data completeness") > doc.index("## Event summary"):
        fail("completeness must come before the numbers it qualifies")
    print("  report   : completeness section first, withheld figures marked, "
          "missing hours per day")
    fake.holes = {}
    print("\n  OK: gaps are reported and withheld, never filled.")


def check_refusal_reason():
    """A 400 carries Open-Meteo's own reason, not just "Bad Request"."""
    import io
    import urllib.error

    body = json.dumps({"error": True, "reason": "Parameter 'end_date' is out "
                       "of allowed range from 1940-01-01 to 2026-09-25"}).encode()

    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request",
                                     {}, io.BytesIO(body))

    real = wg.urllib.request.urlopen
    wg.urllib.request.urlopen = refuse
    try:
        wg._get("https://archive-api.open-meteo.com/v1/archive?x=1")
    except wg.OpenMeteoError as exc:
        if "out of allowed range" not in str(exc) or "400" not in str(exc):
            fail(f"the reason was lost: {exc}")
        print(f"  raised   : {exc}")
    else:
        fail("a 400 did not raise")
    finally:
        wg.urllib.request.urlopen = real
    print("\n  OK: the refusal says why.")


def check_catalogue():
    from core import workflows

    text = workflows.get("fetch_weather").description
    missing = [m for m in wg.MODELS if m not in text]
    if missing or "best_match" not in text:
        fail(f"description omits {missing or 'the best_match refusal'}: {text}")
    print(f"  description names {sorted(wg.MODELS)} and refuses best_match")
    print("\n  OK: the catalogue advertises what the function accepts.")


def check_one_tool(fake):
    from core import logbook, workflows
    from core.monty_graph import run_monty

    _run(fake)
    hourly = fake.query()["hourly"].split(",")
    if any("soil" in v for v in hourly):
        fail(f"soil moisture is still requested: {hourly}")
    if any("soil" in k for k in wg.SPEC):
        fail("soil moisture is still in SPEC")
    print(f"  {len(hourly)} hourly variables requested, no soil moisture")

    names = workflows.names()
    if "fetch_weather" not in names or "fetch_weather_grid" in names             or "fetch_weather_data" in names:
        fail(f"the registry should hold fetch_weather only: {names}")
    print("  registry: fetch_weather; neither old name is registered")

    lines: list[str] = []
    with logbook.collect(lines):
        res = run_monty({"event_id": 1, "tasks": [
            {"function": "fetch_weather_grid",
             "params": {"bbox": BBOX, "start_date": "2026-09-01",
                        "end_date": "2026-09-03", "antecedent_days": 2,
                        "write_figure": False}},
            {"function": "fetch_weather_data",
             "params": {"latitude": 1, "longitude": 1,
                        "start_date": "2026-09-01", "end_date": "2026-09-03"}},
        ]})["results"]
    r1, r2 = res["result_1"], res["result_2"]
    if r1["status"] != "success" or r1["task"] != "fetch_weather":
        fail(f"the old name was not followed to fetch_weather: {r1['task']} "
             f"{r1['status']} {r1.get('message')}")
    if not any("renamed-tool" in ln and "fetch_weather_grid -> fetch_weather" in ln
               for ln in lines):
        fail("following the old name was not logged as renamed-tool")
    print("  fetch_weather_grid -> fetch_weather, logged renamed-tool")
    if r2["status"] != "error" or r2["error"]["error_type"] != "unknown_function"             or "'fetch_weather'" not in r2["message"] or "retired" not in r2["message"]:
        fail(f"the retired point tool should fail naming its replacement: {r2}")
    print(f"  fetch_weather_data refused: {r2['message'][:70]}")
    from core.workflows import weather
    if not callable(weather.fetch_weather_data):
        fail("weather.py's function should still import (file kept, ledger B1)")

    for name in ("fetch_weather", "fetch_weather_grid"):
        if workflows.cache_name(name) != "weather_grid":
            fail(f"{name} should cache as weather_grid, got {workflows.cache_name(name)}")
    if workflows.cache_name("fetch_terrain_data") != "terrain":
        fail("the default cache-name rule changed")
    print("  both names cache as weather_grid; the default rule is unchanged")
    print("\n  OK: one weather tool, the old names handled.")


def check_live_kelani():
    """The cached Kelani grid, re-fetched with the model named."""
    path = REPO_ROOT / "experiments" / "kelani-2025-west" / "inputs" / "weather_grid.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    params, cached = record["params"], record["payload"]
    fresh = wg.fetch_weather(**params, model="ecmwf_ifs")
    for var in ("precipitation", "cloud_cover_model", "wind_speed_10m"):
        a, b = cached["hourly"]["vars"][var]["mean"], fresh["hourly"]["vars"][var]["mean"]
        same = sum(1 for x, y in zip(a, b) if x == y)
        print(f"  {var:18s} {same}/{len(a)} hours identical")
        if same != len(a) or len(a) != len(b):
            fail(f"{var}: ecmwf_ifs does not reproduce the cached series")
    total = (cached["event_summary"]["storm_total_mm"]["aoi_mean"],
             fresh["event_summary"]["storm_total_mm"]["aoi_mean"])
    print(f"  storm total aoi_mean cached {total[0]} mm, ecmwf_ifs {total[1]} mm")
    if total[0] != total[1]:
        fail("storm totals differ")
    # The gap-aware summary must reproduce the old one exactly on complete
    # data -- it changes what happens at a gap, and nothing else.
    if fresh["event_summary"] != cached["event_summary"]:
        diff = [k for k in cached["event_summary"]
                if cached["event_summary"][k] != fresh["event_summary"].get(k)]
        fail(f"the event summary moved on complete data: {diff}")
    if not fresh["qc"]["gaps"]["window_complete"]:
        fail(f"Kelani should audit as complete: {fresh['qc']['gaps']}")
    print("  event summary identical to the cached one; gap audit: complete")
    print("\n  OK: the Kelani weather is ECMWF IFS, reproduced exactly.")


def check_local_clock(fake):
    """The local clock is the event's own zone, never a 0 default (user bug
    2026-11-22): Module 1's stored zone first, else the zone lookup."""
    from core import experiment

    class Store:
        def cached_input(self, name):
            return ({"payload": {"period": {"timezone": "Asia/Colombo"}}}
                    if name == "data_availability" else None)

    experiment.set_current(Store())
    try:
        meta = _run(fake)["meta"]
    finally:
        experiment.set_current(None)
    if (meta["local_utc_offset_h"] != 5.5 or meta["local_timezone"] != "Asia/Colombo"
            or "Module 1" not in meta["local_offset_source"]):
        fail(f"Module 1's zone was not used: {meta}")
    meta = _run(fake)["meta"]                    # no store: the (stubbed) lookup
    if meta["local_utc_offset_h"] != 1.0 or meta["local_timezone"] != "Europe/Test":
        fail(f"the zone lookup was not used: {meta}")
    meta = _run(fake, local_utc_offset_h=0.0)["meta"]
    if meta["local_utc_offset_h"] != 0.0 or meta["local_offset_source"] != "stated by the caller":
        fail(f"a stated offset was not kept: {meta}")
    print("  local clock: Module 1's zone, else the lookup; a stated offset is kept")


def main() -> int:
    from core.workflows import availability as av

    # The zone lookup reaches Open-Meteo; offline it answers a fixed zone.
    real_offset = av.utc_offset
    av.utc_offset = lambda lat, lon, on=None: (1.0, "Europe/Test", "stub lookup")
    try:
        return _main()
    finally:
        av.utc_offset = real_offset


def _main() -> int:
    fake = FakeArchive()
    real_get = wg._get
    wg._get = fake
    try:
        hr("0. THE LOCAL CLOCK IS THE EVENT'S ZONE")
        check_local_clock(fake)
        hr("1. THE REQUEST NAMES THE MODEL")
        check_request(fake)
        hr("2. THE LABEL IS WRITTEN FROM THE REQUEST")
        check_label(fake)
        hr("3. REFUSALS")
        check_refusals(fake)
        hr("4. SPACING FOLLOWS THE MODEL")
        check_spacing(fake)
        hr("5. FORECAST HOURS")
        check_forecast(fake)
        hr("6. GAPS ARE REPORTED, NEVER FILLED")
        check_gaps(fake)
    finally:
        wg._get = real_get
    hr("7. A REFUSAL SAYS WHY")
    check_refusal_reason()
    hr("8. CATALOGUE")
    check_catalogue()
    wg._get = fake
    try:
        hr("9. ONE WEATHER TOOL")
        check_one_tool(fake)
    finally:
        wg._get = real_get

    if "--live" in sys.argv:
        hr("10. LIVE: KELANI REPRODUCED BY ecmwf_ifs")
        check_live_kelani()
    else:
        hr("SKIPPED: live Kelani re-fetch (add --live)")

    hr("RESULT")
    print("  fetch_weather names its model and labels it truthfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
