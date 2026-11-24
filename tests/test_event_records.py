"""The disaster-register lookup: names and links, matched by country and date.

    python tests/test_event_records.py          # canned payloads, no network
    python tests/test_event_records.py --live   # also ask the real registers

Each parser is fed a canned payload in the shape the register returned when
this was built (2026-10-10), so what is tested is the matching rule -- country,
padded window, the status each source reports -- and not what the registers
happen to hold today.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import event_records as er                          # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


SPAN = er.span_of("2025-11-24", "2025-12-01")      # padded: 11-14 .. 12-31

CEMS = {"count": 3, "results": [
    {"code": "EMSR850", "countries": ["Sri Lanka"], "eventTime": "2025-11-27T06:00:00",
     "name": "Flood in Sri Lanka", "centroid": "POINT (80.0 6.9)"},
    {"code": "EMSR700", "countries": ["Sri Lanka"], "eventTime": "2024-06-02T00:00:00",
     "name": "Flood in Sri Lanka 2024", "centroid": "POINT (80.0 6.9)"},
    {"code": "EMSR851", "countries": ["India"], "eventTime": "2025-11-28T00:00:00",
     "name": "Flood in Tamil Nadu", "centroid": "POINT (79.9 7.0)"},
]}

UNOSAT = {"result": {"results": [
    {"name": "unosat-sl-flood-2025", "title": "Flood extent, Sri Lanka",
     "dataset_date": "[2025-12-03T00:00:00 TO 2025-12-03T23:59:59]",
     "groups": [{"display_name": "Sri Lanka"}]},
    {"name": "unosat-in-flood-2025", "title": "Flood extent, India (mentions Sri Lanka)",
     "dataset_date": "[2025-12-01T00:00:00 TO 2025-12-01T23:59:59]",
     "groups": [{"display_name": "India"}]},
    {"name": "unosat-sl-2019", "title": "Old Sri Lanka flood",
     "dataset_date": "[2019-05-01T00:00:00 TO 2019-05-02T23:59:59]",
     "groups": [{"display_name": "Sri Lanka"}]},
]}}

ZKI = """<div class="items">
<div class="event-item" data-country="sri lanka" data-year="2025" data-type="flood">
 <a href="/en/activations/items/ACT160/"><span class="day">29. November 2025</span>
 <h2>Floods in Sri Lanka &amp; Maldives</h2></a></div>
<div class="event-item" data-country="sri lanka" data-year="2021" data-type="flood">
 <a href="/en/activations/items/ACT090/"><span class="day">3. June 2021</span>
 <h2>Old flood</h2></a></div>
<div class="event-item" data-country="india" data-year="2025" data-type="flood">
 <a href="/en/activations/items/ACT161/"><span class="day">28. November 2025</span>
 <h2>Floods in India</h2></a></div>
</div>"""


def with_get(body: str, fn):
    original = er._get
    er._get = lambda url: body
    try:
        return fn()
    finally:
        er._get = original


def test_span() -> None:
    check("the window is padded 10 days before and 30 after",
          SPAN and SPAN[0].isoformat() == "2025-11-14"
          and SPAN[1].isoformat() == "2025-12-31", str(SPAN))
    check("no start, no span", er.span_of("", "") is None)


def test_cems() -> None:
    out = with_get(json.dumps(CEMS), lambda: er.cems("Sri Lanka", SPAN))
    codes = [l["url"] for l in out["links"]]
    check("CEMS: the country's activation in the window matches",
          out["status"] == "matched" and len(out["links"]) == 1
          and codes[0].endswith("/activations/EMSR850/"), str(codes))
    out = with_get(json.dumps(CEMS), lambda: er.cems("", SPAN, (79.8, 6.78, 80.22, 7.08)))
    check("CEMS: with no country, the centroid inside the padded bbox matches",
          {l["url"].split("/")[-2] for l in out["links"]} == {"EMSR850", "EMSR851"},
          str([l["title"] for l in out["links"]]))
    out = with_get(json.dumps(CEMS), lambda: er.cems("Libya", SPAN))
    check("CEMS: nothing for another country is 'none'", out["status"] == "none")


def test_unosat() -> None:
    out = with_get(json.dumps(UNOSAT), lambda: er.unosat("Sri Lanka", SPAN))
    check("UNOSAT: only the country's own dataset in the window",
          [l["url"].rsplit("/", 1)[-1] for l in out["links"]] == ["unosat-sl-flood-2025"],
          str(out["links"]))
    check("UNOSAT: no country is 'skipped', not 'none'",
          er.unosat("", SPAN)["status"] == "skipped")


def test_zki() -> None:
    out = with_get(ZKI, lambda: er.zki("Sri Lanka", SPAN))
    check("ZKI: the country's activation in the window, title unescaped",
          [l["title"] for l in out["links"]] == ["Floods in Sri Lanka & Maldives"]
          and out["links"][0]["url"].endswith("/en/activations/items/ACT160/"),
          str(out["links"]))


def test_lookup_rules() -> None:
    def boom(country, span, bbox):
        raise TimeoutError("slow register")

    def hit(country, span, bbox):
        return er._result(er.GDACS, "matched", [er._link("x", "https://e/x")])

    out = er.lookup("Sri Lanka", "2025-11-24", "2025-12-01",
                    searched=[(er.GDACS, hit), (er.CEMS, boom)], linked=[(er.NASA, er.nasa_disasters)])
    status = {r["source"]: r["status"] for r in out}
    check("one failing source never stops the others",
          status == {er.GDACS: "matched", er.CEMS: "unreachable", er.NASA: "catalogue"},
          str(status))
    check("an unreachable source carries the reason",
          "TimeoutError" in next(r["note"] for r in out if r["source"] == er.CEMS))

    out = er.lookup("Sri Lanka", "", "",
                    searched=[(er.GDACS, hit)], linked=[(er.NASA, er.nasa_disasters)])
    status = {r["source"]: r["status"] for r in out}
    check("no window: searchable sources are skipped, catalogues still listed",
          status == {er.GDACS: "skipped", er.NASA: "catalogue"}, str(status))

    many = er._result(er.CEMS, "matched", [er._link(str(i), f"https://e/{i}") for i in range(8)])
    check("at most five links per source, the rest counted",
          len(many["links"]) == er.MAX_LINKS and many["more"] == 3)


def test_render() -> None:
    records = [
        er._result(er.CEMS, "matched", [er._link("EMSR850 -- Flood in Sri Lanka",
                                                 "https://mapping.emergency.copernicus.eu/activations/EMSR850/")]),
        er._result(er.GDACS, "none"),
        er._result(er.DFO, "unreachable", note="URLError; no event search, archive index only"),
        er._result(er.NASA, "catalogue", note="no event search"),
    ]
    text = er.render(records, "Kelani basin", "2025-11-24 to 2025-12-01")
    check("the section names its scope", "Event records for Kelani basin around 2025-11-24" in text)
    check("it warns that matches are by country and date",
          "not by event" in text)
    check("a match carries its link",
          "https://mapping.emergency.copernicus.eu/activations/EMSR850/" in text)
    check("every source appears, with its catalogue link",
          all(r["catalogue"] in text or r["status"] == "matched" for r in records)
          and all(r["source"] in text for r in records))
    check("an unreachable source says so, and why",
          "could not be reached (URLError" in text)
    check("nothing to render, nothing appended", er.render([]) == "")


def test_live() -> None:
    out = er.lookup("Sri Lanka", "2025-11-24", "2025-12-01", (79.8, 6.783, 80.217, 7.083))
    for rec in out:
        tag = PASS if rec["status"] in ("matched", "none", "catalogue") else SKIP
        results.append((tag, f"live {rec['source']}", rec["status"]))
        print(f"[{tag}] live {rec['source']}: {rec['status']} "
              f"{[l['url'] for l in rec['links']]} {rec['note']}")


def main() -> None:
    test_span()
    test_cems()
    test_unosat()
    test_zki()
    test_lookup_rules()
    test_render()
    if "--live" in sys.argv:
        test_live()

    failed = [r for r in results if r[0] == FAIL]
    passed = sum(1 for r in results if r[0] == PASS)
    print(f"\n{passed}/{len(results)} passed"
          + (f", {len(results) - passed - len(failed)} skipped" if len(results) - passed - len(failed) else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
