"""Modules 9-10 vulnerability: INFORM meters, trend, income group, earlier floods.

    python tests/test_inform_figures.py      # no model, no network, no cost

    1. meters   -- four meters in order, climate change = 2050 pessimistic;
                   a missing climate table is "not known", never 0
    2. drawn    -- the meters and the trend are drawn; a trend year the API
                   did not answer is a gap, not a bar
    3. income   -- "Upper middle income" reads "an upper-middle-income country"
    4. lines    -- the meters' line and the trend line carry the numbers
    5. floods   -- one line: the count and the latest event, no list
    6. vault    -- both live templates place the new blocks and figures
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core import charts_inform, module_blocks  # noqa: E402

results: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(("PASS" if ok else "FAIL", name))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


INF = {
    "country": "Sri Lanka",
    "release": {"name": "INFORM Risk Mid 2025", "published": "2025-08-28"},
    "scores": {"inform_risk": {"value": 3.3}, "hazard_exposure": {"value": 2.8},
               "vulnerability": {"value": 2.6}, "lack_of_coping_capacity": {"value": 5.0}},
    "climate_change": {"status": "ok", "risk_index": {"baseline": 3.4, "2050_pessimistic": 3.6,
                                                      "2050_optimistic": 3.4}},
    "income_group": {"value": "Upper middle income", "retrieved_at": "2026-11-11T05:28:04Z"},
    "trend": {"status": "ok", "series": "INFORM Risk 2023", "published": "2022-08-31",
              "years": [{"year": 2014, "status": "ok", "vulnerability": 4.1,
                         "lack_of_coping_capacity": 4.5, "natural_hazard": 5.2},
                        {"year": 2015, "status": "NOT KNOWN -- RemoteDisconnected"},
                        {"year": 2016, "status": "ok", "vulnerability": 3.8,
                         "lack_of_coping_capacity": 4.2, "natural_hazard": 5.2}]},
}


class FakeCtx:
    def __init__(self, inf=None, disasters=None, root=None):
        self._data = {"inform": inf, "disasters": disasters}
        self.store = SimpleNamespace(dir=str(root or "."), memory_path=None, bbox=None)
        self.figures, self.details, self.rests_on = [], {}, []

    def get(self, role):
        return self._data.get(role)

    def rest(self, name, payload):
        self.rests_on.append((name, payload))


def main() -> int:
    # 1. meters
    m = charts_inform.meter_values(INF)
    check("meters: four, in order", [x["key"] for x in m] == ["inform_risk", "vulnerability",
                                                             "lack_of_coping_capacity",
                                                             "climate_change_2050"])
    check("meters: climate = 2050 pessimistic", m[3]["value"] == 3.6)
    no_cc = copy.deepcopy(INF)
    no_cc["climate_change"] = {"status": "NOT KNOWN -- name mismatch"}
    check("meters: missing climate is not known, not 0", charts_inform.meter_values(no_cc)[3]["value"] is None)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 2. drawn
        p1 = charts_inform.meters(INF, tmp / "m.png", "Source: test")
        p2 = charts_inform.trend(INF, tmp / "t.png", "Source: test")
        check("drawn: meters and trend", p1.is_file() and p2 is not None and p2.is_file())
        check("drawn: no trend, no figure", charts_inform.trend({"trend": {}}, tmp / "x.png") is None)

        # 3. income
        c = FakeCtx(INF, root=tmp)
        f = module_blocks.b_hev_income_group(c)
        check("income: phrase", "an **upper-middle-income** country" in f.text, f.text)
        old = copy.deepcopy(INF)
        old.pop("income_group")
        check("income: an old payload is NOT KNOWN",
              module_blocks.b_hev_income_group(FakeCtx(old, root=tmp)).status == "not_known")

        # 4. lines
        line = module_blocks.b_hev_inform(FakeCtx(INF, root=tmp)).text
        check("lines: meters line", "**INFORM risk (overall) 3.3**" in line and "vulnerability 2.6" in line
              and "higher is worse" in line, line)
        tl = module_blocks.b_hev_inform_trend_line(FakeCtx(INF, root=tmp)).text
        check("lines: trend line names the gap", "4.1 to 3.8**" in tl and "no data for 2015" in tl, tl)

        # 5. floods
        d = {"search_cutoff": "2025-11-23", "events": [
            {"name": "Flood A", "start": "2016-05-15", "kind": "flood", "source": "GDACS", "cite": "GDACS, 2016"},
            {"name": "Cyclone B", "start": "2024-11-27", "kind": "cyclone", "source": "GDACS",
             "cite": "GDACS, 2024", "km_from_aoi": 120.4},
            {"name": "Quake C", "start": "2025-01-01", "kind": "earthquake", "source": "GDACS"}]}
        fl = module_blocks.b_hev_previous_floods(FakeCtx(disasters=d, root=tmp)).text
        check("floods: count and latest, no list",
              fl.startswith("**2 floods and storms** recorded before 2025-11-23; the latest: **Cyclone B, "
                            "2024-11-27**, 120 km") and "\n-" not in fl, fl)

    # 6. vault
    from core import report_render

    for slot in ("essential_services_hev", "human_health_risks_hev"):
        t = report_render.template(slot)
        want = ["{{fact:hev_income_group}}", "{{figure:hev_inform_meters}}", "{{fact:hev_inform}}",
                "{{figure:hev_inform_trend}}", "{{fact:hev_inform_trend_line}}",
                "{{fact:hev_previous_floods}}", "{{figure:previous_disasters_timeline}}"]
        pos = [t.find(w) for w in want]
        check(f"vault: {slot} places them in order", all(p >= 0 for p in pos) and pos == sorted(pos),
              str(pos))

    failed = [n for s, n in results if s == "FAIL"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
