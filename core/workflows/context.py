"""National context: World Bank indicators and INFORM risk.

Two tools, both national and both labelled national. They describe how
vulnerable a country is, not what happened in an AOI, and a report that put a
national GDP beside a district flood without saying so would invite a reader
to divide one by the other.

`fetch_inform_risk` takes `before`: the INFORM release used is the latest one
**published before** that date. A report about a November 2025 flood should
describe the country as it was assessed before the flood, not a release that
may already carry the event's own impact -- the same reasoning as the
before-the-window rule for previous disasters (D-15).

Sources and vintages: docs/SOURCES.md section 8f.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from . import tool
from . import _fetch as F

WB_API = "https://api.worldbank.org/v2"
WB_INDICATORS = {"gdp_usd": ("NY.GDP.MKTP.CD", "GDP (current US$)", "US$"),
                 "population": ("SP.POP.TOTL", "Population, total", "people")}

INFORM_API = "https://drmkc.jrc.ec.europa.eu/Inform-Index/API/InformAPI"
# The four headline numbers, by the API's indicator id. "CC" is the Lack of
# Coping Capacity dimension (higher is worse), whatever its display name says.
INFORM_HEADLINE = {"inform_risk": "INFORM", "hazard_exposure": "HA",
                   "vulnerability": "VU", "lack_of_coping_capacity": "CC"}
INFORM_CC_URL = ("https://drmkc.jrc.ec.europa.eu/inform-index/Portals/0/InfoRM/2022/"
                 "INFORM%20CC%20Brochure%20data.xlsx")
# Column layout of the 2022 brochure sheet, checked against its header row on
# every read: (column index, header text, horizon, scenario).
INFORM_CC_COLUMNS = [(1, "INFORM CC Risk Index 2022", "baseline", ""),
                     (2, "INFORM CC Risk Index", "2050", "pessimistic"),
                     (5, "INFORM CC Risk Index", "2050", "optimistic"),
                     (8, "INFORM CC Risk Index", "2080", "pessimistic"),
                     (11, "INFORM CC Risk Index", "2080", "optimistic")]


def _iso3(value: str) -> str:
    code = (value or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(f"country_iso3 must be a three-letter ISO code, got {value!r}")
    return code


def iso3_from_iso2(iso2: str) -> str:
    """The ISO3 code for an ISO2 one, from the World Bank's country record."""
    code = (iso2 or "").strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"not a two-letter ISO code: {iso2!r}")
    data = F.get_json(f"{WB_API}/country/{code}?format=json")
    rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
    if not rows or not rows[0].get("id"):
        raise ValueError(f"the World Bank knows no country {code!r}")
    return str(rows[0]["id"]).upper()


def country_record(iso3: str) -> dict[str, Any]:
    """The World Bank's country record: its English name (the join key for
    sources keyed by name) and its CURRENT income group."""
    data = F.get_json(f"{WB_API}/country/{_iso3(iso3)}?format=json")
    rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
    if not rows:
        raise ValueError(f"the World Bank knows no country {iso3!r}")
    return rows[0]


def country_name(iso3: str) -> str:
    return country_record(iso3)["name"]


@tool(
    "fetch_world_bank_indicators",
    "National GDP (current US$, NY.GDP.MKTP.CD) and national population "
    "(SP.POP.TOTL) for a country, each the latest year with a value, from the "
    "World Bank API. NATIONAL figures only -- never an AOI's. `country_iso3` "
    "is a three-letter ISO code, e.g. LKA.",
    label="National GDP and population",
    source="World Bank WDI",
    order=60,
    data_source_uncertainty=(
        "National figures in the latest year with a value; they are estimates and "
        "can be revised."),
    method_uncertainty=(
        "Each indicator takes its own latest year, so GDP and population can be "
        "from different years, and neither is the AOI's."),
)
def fetch_world_bank_indicators(country_iso3: str) -> dict[str, Any]:
    iso3 = _iso3(country_iso3)
    out: dict[str, Any] = {"country_iso3": iso3, "scope": "national", "indicators": {}}
    inputs, updated = [], ""
    for key, (code, label, unit) in WB_INDICATORS.items():
        data = F.get_json(f"{WB_API}/country/{iso3}/indicator/{code}"
                          f"?format=json&mrnev=1&per_page=5")
        meta = data[0] if isinstance(data, list) and data else {}
        rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
        updated = meta.get("lastupdated", updated)
        if not rows or rows[0].get("value") is None:
            out["indicators"][key] = {"code": code, "name": label, "value": None,
                                      "year": None, "unit": unit,
                                      "status": "NOT KNOWN -- no value served"}
            continue
        row = rows[0]
        out["country"] = row["country"]["value"]
        out["indicators"][key] = {"code": code, "name": label, "value": row["value"],
                                  "year": int(row["date"]), "unit": unit, "status": "ok"}
        inputs.append(F.item(f"{key}_vintage",
                             f"{label} is the latest year with a value ({row['date']}), "
                             f"national, from the World Bank database updated {updated}.",
                             int(row["date"]), "year", "stated_by_source", row["date"]))
    inputs.append(F.item("national_only", "These are national figures; they say "
                         "nothing about the AOI itself.", None, "", "stated_by_source"))
    out["database_updated"] = updated
    out["uncertainty"] = F.blocks(inputs)
    out["sources"] = [F.source("world-bank-wdi", "World Development Indicators",
                               "World Bank", WB_API, updated, "CC BY 4.0")]
    return out


def _published(workflow: dict[str, Any]) -> str:
    return str(workflow.get("FlagGnaPublished") or workflow.get("WorkflowDate") or "")[:10]


def _workflows(year: int, seen: dict[int, Any]) -> list[dict[str, Any]]:
    """One year's INFORM workflows, read once per tool call (`seen` lives for
    that call only: an uncached run never reuses another run's answer). A
    failure is remembered too, so the year is not asked twice in one call."""
    if year not in seen:
        try:
            seen[year] = F.get_json(f"{INFORM_API}/Workflows/GetByYear/{year}") or []
        except Exception as exc:                               # noqa: BLE001
            seen[year] = exc
    if isinstance(seen[year], Exception):
        raise seen[year]
    return seen[year]


def _risk_workflow(before: date, seen: dict[int, Any] | None = None
                   ) -> tuple[dict[str, Any], list[str]]:
    """The latest INFORM Risk release published before `before`, and what was
    looked at. Years the API drops the connection for are listed, not hidden."""
    seen = {} if seen is None else seen
    candidates, problems = [], []
    for year in range(before.year, before.year - 4, -1):
        try:
            rows = _workflows(year, seen)
        except Exception as exc:                               # noqa: BLE001
            problems.append(f"{year}: {type(exc).__name__}")
            continue
        candidates += [w for w in rows or []
                       if str(w.get("Name", "")).startswith("INFORM Risk")
                       and w.get("FlagGnaPublished")
                       and _published(w) < before.isoformat()]
    if not candidates:
        raise ValueError(f"no INFORM Risk release published before {before} was "
                         f"found (years unreadable: {problems or 'none'})")
    return max(candidates, key=_published), problems


# The trend: INFORM re-computes past years with ONE release's method (workflow
# names "INFORM Risk <release> - <year>"); successive releases change the
# method, so only one such series is a trend. Natural hazard = HA.NAT, the
# natural-hazard half of Hazard & Exposure (all natural hazards together).
INFORM_TREND = {"vulnerability": "VU", "lack_of_coping_capacity": "CC",
                "natural_hazard": "HA.NAT"}
_SERIES = re.compile(r"^INFORM Risk (\d{4}) - (\d{4})$")


def _trend(iso3: str, before: date, seen: dict[int, Any] | None = None) -> dict[str, Any]:
    """The latest back-calculated INFORM series published before `before`,
    plus that release's own year. A year the API does not answer is a gap,
    named, never filled."""
    seen = {} if seen is None else seen
    found: dict[int, list[tuple[int, int, str]]] = {}
    headline: dict[int, tuple[int, str]] = {}
    unreadable = []
    for year in range(2012, before.year + 1):
        try:
            rows = _workflows(year, seen)
        except Exception:                                      # noqa: BLE001
            unreadable.append(str(year))
            continue
        for w in rows:
            pub = _published(w)
            if not pub or date.fromisoformat(pub) >= before:
                continue
            if m := _SERIES.match(w.get("Name", "")):
                found.setdefault(int(m[1]), []).append((int(m[2]), w["WorkflowId"], pub))
            elif re.fullmatch(r"INFORM Risk (\d{4})", w.get("Name", "")):
                headline[int(w["Name"][-4:])] = (w["WorkflowId"], pub)
    if not found:
        return {"status": "NOT KNOWN -- no back-calculated INFORM series was published before "
                          f"{before}", "unreadable_years": unreadable}
    release = max(found)
    points = sorted(found[release])
    if release in headline:                  # the release's own year, same method
        points.append((release, *headline[release]))
    years: list[dict[str, Any]] = []
    for year, wid, pub in points:
        try:
            by_id = {s["IndicatorId"]: s for s in
                     F.get_json(f"{INFORM_API}/Countries/Scores/?WorkflowId={wid}&Iso3={iso3}") or []}
        except Exception as exc:                               # noqa: BLE001
            years.append({"year": year, "workflow_id": wid, "status":
                          f"NOT KNOWN -- {type(exc).__name__}"[:120]})
            continue
        years.append({"year": year, "workflow_id": wid, "status": "ok",
                      **{k: (by_id.get(ind) or {}).get("IndicatorScore")
                         for k, ind in INFORM_TREND.items()}})
    return {"status": "ok", "series": f"INFORM Risk {release}", "published": points[-1][2],
            "indicators": dict(INFORM_TREND), "years": years, "unreadable_years": unreadable}


def _climate(name: str) -> dict[str, Any]:
    """INFORM Climate Change 2022 for one country, from the brochure sheet."""
    from openpyxl import load_workbook

    path, meta = F.cached_file(INFORM_CC_URL, "inform_cc", "INFORM_CC_Brochure_data_2022.xlsx")
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[4]
    for col, text, _, _ in INFORM_CC_COLUMNS:
        if str(header[col] or "").strip() != text:
            raise ValueError(f"INFORM CC sheet layout changed: column {col} is "
                             f"{header[col]!r}, expected {text!r}")
    row = next((r for r in rows[5:] if str(r[0] or "").strip() == name), None)
    if row is None:
        return {"status": f"NOT KNOWN -- {name!r} is not in the INFORM CC 2022 table",
                "file": meta}
    return {"status": "ok", "release": "INFORM Climate Change 2022",
            "scenarios": {"pessimistic": "RCP 8.5 + SSP3", "optimistic": "RCP 4.5 + SSP1"},
            "risk_index": {f"{h}_{sc}" if sc else h: row[col]
                           for col, _, h, sc in INFORM_CC_COLUMNS},
            "file": {k: meta[k] for k in ("url", "retrieved_at", "sha256", "cached")}}


@tool(
    "fetch_inform_risk",
    "INFORM Risk for a country (0-10): the overall risk, Hazard & Exposure, "
    "Vulnerability and Lack of Coping Capacity, from the latest INFORM Risk "
    "release PUBLISHED BEFORE `before` (ISO date; default today), and INFORM "
    "Climate Change 2022 projections for 2050 and 2080 under a pessimistic and "
    "an optimistic scenario; the yearly trend of Vulnerability, Lack of Coping "
    "Capacity and Natural Hazard (all natural hazards) from INFORM's latest "
    "back-calculated series; and the World Bank income group (current). "
    "NATIONAL scores. `country_iso3` e.g. LKA.",
    label="INFORM risk (national)",
    source="EC JRC INFORM",
    order=61,
    data_source_uncertainty=(
        "A national composite index (0-10) built from many indicators; the 2022 "
        "climate projections depend on the chosen scenarios."),
    method_uncertainty=(
        "It uses the release published before the event date, and climate-change "
        "scores are matched by country name, which fails if the names differ. The "
        "trend is one release's re-computed series, so it ends before the current "
        "release; the income group is today's, not the event date's."),
)
def fetch_inform_risk(country_iso3: str, before: str = "") -> dict[str, Any]:
    iso3 = _iso3(country_iso3)
    cutoff = date.fromisoformat(before) if before else date.today()
    seen: dict[int, Any] = {}             # this call's INFORM year lists, read once
    workflow, unreadable = _risk_workflow(cutoff, seen)
    wid = workflow["WorkflowId"]
    scores = F.get_json(f"{INFORM_API}/Countries/Scores/?WorkflowId={wid}&Iso3={iso3}")
    by_id = {s["IndicatorId"]: s for s in scores or []}
    headline = {}
    for key, ind in INFORM_HEADLINE.items():
        s = by_id.get(ind)
        headline[key] = ({"value": s["IndicatorScore"], "unit": s.get("Unit") or "0-10",
                          "indicator_id": ind, "status": "ok"} if s else
                         {"value": None, "indicator_id": ind,
                          "status": "NOT KNOWN -- not in this release's scores"})
    river = by_id.get("HA.NAT.FL")
    record = country_record(iso3)
    name = record["name"]
    income = (record.get("incomeLevel") or {}).get("value") or None
    try:
        climate = _climate(name)
    except Exception as exc:                                   # noqa: BLE001
        climate = {"status": f"NOT KNOWN -- {type(exc).__name__}: {exc}"[:200]}
    try:
        trend = _trend(iso3, cutoff, seen)
    except Exception as exc:                                   # noqa: BLE001
        trend = {"status": f"NOT KNOWN -- {type(exc).__name__}: {exc}"[:200]}

    published = _published(workflow)
    inputs = [
        F.item("inform_release", f"{workflow['Name']} (published {published}), the "
               f"latest INFORM Risk release published before {cutoff}.",
               workflow["Name"], "", "stated_by_source", published),
        F.item("national_only", "INFORM scores are national; they describe the "
               "country, not the AOI.", None, "", "stated_by_source"),
        F.item("risk_class", "The INFORM API serves no risk class, so none is "
               "reported.", None, "", "stated_by_source"),
    ]
    if climate.get("status") == "ok":
        inputs.append(F.item("climate_release", "INFORM Climate Change is the 2022 "
                             "release; its baseline is not the current INFORM Risk.",
                             2022, "year", "stated_by_source", "2022"))
    inputs.append(F.item("income_group", "The income group is the World Bank's CURRENT "
                         "classification (country record, read today), not necessarily the "
                         "one in force at the event date.", income, "", "stated_by_source"))
    if trend.get("status") == "ok":
        inputs.append(F.item("trend_series", f"The trend is the {trend['series']} series: "
                             "earlier years re-computed with that release's method, so the "
                             "years compare; it ends before the release the scores above come "
                             "from.", trend["series"], "", "stated_by_source",
                             trend["published"]))
    tool_items = ([F.item("unreadable_years", f"INFORM workflow years the API did not "
                          f"answer: {', '.join(unreadable)}; a later release may "
                          f"have been missed.", len(unreadable), "years", "measured")]
                  if unreadable else [])
    gaps = [str(y["year"]) for y in trend.get("years") or [] if y.get("status") != "ok"]
    if gaps:
        tool_items.append(F.item("trend_gaps", f"Trend years the API did not answer: "
                                 f"{', '.join(gaps)}; shown as gaps, not filled.",
                                 len(gaps), "years", "measured"))
    return {
        "country_iso3": iso3, "country": name, "scope": "national",
        "before": cutoff.isoformat(),
        "release": {"workflow_id": wid, "name": workflow["Name"], "published": published},
        "scores": headline,
        "river_flood_hazard": ({"value": river["IndicatorScore"], "unit": "0-10"}
                               if river else None),
        "climate_change": climate,
        "income_group": ({"value": income, "id": (record.get("incomeLevel") or {}).get("id"),
                          "source": "World Bank country record", "retrieved_at": F._now()}
                         if income else {"value": None, "status": "NOT KNOWN -- the World Bank "
                                                                   "record has no income group"}),
        "trend": trend,
        "uncertainty": F.blocks(inputs, tool_items),
        "sources": [F.source("inform-risk", workflow["Name"], "EC JRC DRMKC",
                             INFORM_API, published),
                    F.source("inform-cc-2022", "INFORM Climate Change 2022",
                             "EC JRC DRMKC", INFORM_CC_URL, "2022", role="supporting"),
                    F.source("world-bank-country", "World Bank country record (income group)",
                             "World Bank", f"{WB_API}/country/{iso3}", "current",
                             "CC BY 4.0", role="supporting")]
                   + ([F.source("inform-trend", f"{trend['series']} back-calculated series",
                                "EC JRC DRMKC", INFORM_API, trend["published"], role="supporting")]
                      if trend.get("status") == "ok" else []),
    }
