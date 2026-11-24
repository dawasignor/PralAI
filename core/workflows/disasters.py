"""Previous disasters, searched BEFORE the event window only.

The event period itself is never searched. A report that described "previous
floods" from a register queried over the flood's own dates would be reading
the event into its own history -- and the search engines rank the current
event first, which is the bias D-15 exists to stop. So `before` is a hard
cutoff: every source is asked for records that END on or before it, and
anything reaching past it is dropped and counted.

No casualty figures are kept in this version (D-15).

**Who may be a source** (user decision 2026-11-01): national news and
government reports, or internationally recognised bodies -- Copernicus EMS,
the World Bank, WHO, UN bodies. Every hit carries a short citation, the
publisher and the year ("World Bank, 2017"), and `references` lists each one
once. Sources (docs/SOURCES.md 8, 8f):

* **GDACS** (EC JRC and UN OCHA) -- by country, every alert level (the API's
  default is orange and red only, which hides most floods).
* **Copernicus EMS Rapid Mapping** -- activations by country; the public list
  covers activations from 2023 on.
* **UNOSAT** -- its products as published on HDX.
* **World Bank Documents & Reports** -- post-disaster needs assessments,
  damage and recovery reports naming the country. Dated by the document, not
  the event.

**National news and government reports have no source yet** (ReliefWeb, the
one route to them, was removed on 2026-11-01: it needs an approved appname).
The payload reports them as `not_searched`, never as "nothing found".
"""

from __future__ import annotations

import math
import re
import urllib.parse
from datetime import date, timedelta
from typing import Any

from . import tool
from . import _fetch as F

GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?"
GDACS_PAGE = 100
CEMS_LIST = ("https://rapidmapping.emergency.copernicus.eu/backend/"
             "dashboard-api/public-activations-info/?limit=1000")
CEMS_PAGE = "https://mapping.emergency.copernicus.eu/activations/{code}/"
HDX_SEARCH = "https://data.humdata.org/api/3/action/package_search?"
HDX_PAGE = "https://data.humdata.org/dataset/{name}"
WB_DOCS = "https://search.worldbank.org/api/v3/wds?"
HAZARDS = {"FL": "flood", "TC": "tropical cyclone", "EQ": "earthquake",
           "DR": "drought", "VO": "volcano", "WF": "wildfire", "TS": "tsunami"}

# World Bank: only documents that record an event -- not project paperwork
# (procurement plans, status reports, audits), which the search returns most.
WB_TYPES = {"Publication", "Working Paper", "Policy Research Working Paper",
            "Report", "Brief", "Economic & Sector Work"}
WB_HAZARD = re.compile(r"flood|cyclone|landslide|drought|tsunami|storm|monsoon|"
                       r"earthquake|disaster", re.I)
WB_EVENT = re.compile(r"post[- ]disaster|needs assessment|damage|recovery|"
                      r"impact assessment|disaster response|emergency", re.I)
KINDS = re.compile(r"flood|cyclone|landslide|drought|tsunami|earthquake|storm|"
                   r"wildfire|fire|volcan", re.I)


def _km_to_bbox(lon: float, lat: float, bbox: list[float]) -> float:
    w, s, e, n = bbox
    dx = max(w - lon, 0, lon - e) * 111.32 * math.cos(math.radians((s + n) / 2))
    dy = max(s - lat, 0, lat - n) * 110.574
    return math.hypot(dx, dy)


def _cite(publisher: str, when: str) -> str:
    """The short citation, e.g. "World Bank, 2017"."""
    return f"{publisher}, {when[:4]}" if when[:4].isdigit() else publisher


def _hit(source: str, publisher: str, name: str, kind: str, start: str, end: str = "",
         url: str = "", **extra: Any) -> dict[str, Any]:
    return {"source": source, "publisher": publisher, "cite": _cite(publisher, start),
            "name": " ".join(str(name).split()), "kind": kind, "start": start[:10],
            "end": (end or start)[:10], "alert_level": "", "glide": "",
            "lon": None, "lat": None, "url": url, **extra}


def _kind(text: str) -> str:
    if re.search(r"water extent", text or "", re.I):
        return "flood"
    found = KINDS.search(text or "")
    return found.group(0).lower() if found else ""


def _gdacs(country: str, start: date, end: date) -> tuple[list[dict[str, Any]], bool]:
    params = [("fromDate", start.isoformat()), ("toDate", end.isoformat()),
              ("country", country), ("alertlevel", "Green;Orange;Red"),
              ("pagesize", str(GDACS_PAGE))]
    data = F.get_json(GDACS_URL + urllib.parse.urlencode(params))
    out = []
    for feature in data.get("features") or []:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        out.append(_hit("GDACS", "GDACS", props.get("name") or props.get("description", ""),
                        HAZARDS.get(props.get("eventtype", ""), props.get("eventtype", "")),
                        props.get("fromdate") or "", props.get("todate") or "",
                        (props.get("url") or {}).get("report", ""),
                        alert_level=str(props.get("alertlevel", "")).lower(),
                        glide=props.get("glide", ""),
                        lon=coords[0] if isinstance(coords[0], (int, float)) else None,
                        lat=coords[1] if isinstance(coords[1], (int, float)) else None))
    return out, len(out) >= GDACS_PAGE


def _cems(country: str, start: date, end: date) -> list[dict[str, Any]]:
    data = F.get_json(CEMS_LIST)
    out = []
    for item in data.get("results") or []:
        when = str(item.get("eventTime") or "")[:10]
        if country.lower() not in [c.lower() for c in item.get("countries") or []]:
            continue
        if not when or not start.isoformat() <= when <= end.isoformat():
            continue
        point = re.match(r"POINT \(([-\d.]+) ([-\d.]+)\)", item.get("centroid") or "")
        out.append(_hit("Copernicus EMS", "Copernicus EMS",
                        f"{item.get('code', '')} {item.get('name', '')}",
                        _kind(item.get("name", "")) or str(item.get("category") or "").lower(),
                        when, url=CEMS_PAGE.format(code=item.get("code", "")),
                        lon=float(point[1]) if point else None,
                        lat=float(point[2]) if point else None))
    return out


def _unosat(country: str, start: date, end: date) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"fq": "organization:unosat", "q": country,
                                    "rows": 200, "sort": "metadata_created desc"})
    data = F.get_json(HDX_SEARCH + query)
    out = []
    for item in (data.get("result") or {}).get("results") or []:
        days = re.findall(r"\d{4}-\d{2}-\d{2}", item.get("dataset_date") or "")
        if not days:
            continue
        # Full-text search on the country name also hits a neighbour's dataset
        # that mentions it; the dataset's own country groups decide.
        groups = [g.get("display_name", "").lower() for g in item.get("groups") or []]
        if groups and country.lower() not in groups:
            continue
        if not start.isoformat() <= days[0] <= end.isoformat():
            continue
        title = item.get("title") or item.get("name", "")
        out.append(_hit("UNOSAT", "UNOSAT", title, _kind(title), days[0], days[-1],
                        HDX_PAGE.format(name=item.get("name", ""))))
    return out


def _world_bank(country: str, start: date, end: date) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({
        "format": "json", "count_exact": country, "rows": 200,
        "qterm": "flood OR cyclone OR landslide OR drought OR tsunami OR disaster",
        "fl": "docdt,docty,display_title,url",
        "strdate": start.isoformat(), "enddate": end.isoformat()})
    data = F.get_json(WB_DOCS + query)
    out = []
    for key, doc in (data.get("documents") or {}).items():
        if key == "facets" or not isinstance(doc, dict):
            continue
        title = doc.get("display_title") or ""
        when = str(doc.get("docdt") or "")[:10]
        if (doc.get("docty") not in WB_TYPES or country.lower() not in title.lower()
                or not WB_HAZARD.search(title) or not WB_EVENT.search(title)):
            continue
        if not when or not start.isoformat() <= when <= end.isoformat():
            continue
        out.append(_hit("World Bank", "World Bank", title, _kind(title), when,
                        url=doc.get("url") or "", document_type=doc.get("docty")))
    return out


@tool(
    "search_previous_disasters",
    "Disasters recorded in a country BEFORE an event, never during it, from "
    "national news and government reports or internationally recognised bodies: "
    "GDACS, Copernicus EMS activations, UNOSAT products, World Bank post-disaster "
    "assessments. National news and government reports are not searched yet. "
    "Records "
    "ending on or before `before` (ISO date -- pass the event window's start "
    "minus one day), over the preceding `years` (default 20). Each hit has its "
    "dates, hazard, link and a short citation (e.g. \"World Bank, 2017\"). No "
    "casualty figures. With `bbox`, a hit with a location carries its distance "
    "from the AOI. National search.",
    label="Previous disasters (before the event)",
    source="GDACS, Copernicus EMS, UNOSAT, World Bank",
    order=63,
    data_source_uncertainty=(
        "What: registers of events large enough to raise an alert, trigger a "
        "mapping activation or a World Bank assessment, so small and local floods "
        "are missing; national news and government reports are not searched, and "
        "the Copernicus EMS public list starts in 2023. Affects: the history "
        "reads as fewer and larger events than happened. Reduce: add a national "
        "source -- the disaster-loss database (DesInventar) where the government "
        "keeps one, or its disaster agency's situation reports."),
    method_uncertainty=(
        "A national search by date before the event, one row per record and "
        "publisher, so one event can appear once per source; distance to the AOI "
        "is computed where a record has a location, but nothing is filtered by "
        "it, and casualty figures are not kept."),
)
def search_previous_disasters(country_iso3: str, before: str, region: str = "",
                              bbox: list[float] | None = None,
                              years: int = 20) -> dict[str, Any]:
    from .context import _iso3, country_name

    iso3 = _iso3(country_iso3)
    if not before:
        raise ValueError("`before` is required: the search must stop before the "
                         "event window (D-15); pass the window start minus one day")
    cutoff = date.fromisoformat(before)
    if not 1 <= int(years) <= 50:
        raise ValueError("years must be between 1 and 50")
    start = cutoff - timedelta(days=round(365.25 * int(years)))
    name = country_name(iso3)

    status: dict[str, dict[str, Any]] = {}
    hits: list[dict[str, Any]] = []

    def search(key: str, fn, *args) -> None:
        try:
            found = fn(*args)
            full = False
            if isinstance(found, tuple):
                found, full = found
            hits.extend(found)
            status[key] = {"status": "matched" if found else "none", "count": len(found),
                           **({"page_full": True} if full else {})}
        except Exception as exc:                               # noqa: BLE001
            status[key] = {"status": "unreachable",
                           "problem": f"{type(exc).__name__}: {exc}"[:200]}

    search("gdacs", _gdacs, name, start, cutoff)
    search("copernicus_ems", _cems, name, start, cutoff)
    search("unosat", _unosat, name, start, cutoff)
    search("world_bank", _world_bank, name, start, cutoff)
    status["national_news_and_government"] = {
        "status": "not_searched",
        "problem": "no national news or government source is wired in yet"}

    # The hard cutoff. A record whose end (or start, when it has no end) is
    # after `before` touches the event period and is dropped -- counted, not
    # silently removed, so the reader knows the filter did something.
    kept, dropped = [], 0
    for h in hits:
        last = h["end"] or h["start"]
        if not last or last > cutoff.isoformat():
            dropped += 1
            continue
        if bbox is not None and h["lon"] is not None and h["lat"] is not None:
            h["km_from_aoi"] = round(_km_to_bbox(h["lon"], h["lat"], [float(v) for v in bbox]), 1)
        kept.append(h)
    kept.sort(key=lambda h: h["start"], reverse=True)

    references: dict[tuple[str, str], dict[str, str]] = {}
    for h in kept:
        references.setdefault((h["cite"], h["name"]), {
            "cite": h["cite"], "title": h["name"], "publisher": h["publisher"],
            "url": h["url"], "via": h["source"]})

    searched = [k for k, v in status.items() if v["status"] in ("matched", "none")]
    inputs = [F.item("search_cutoff", f"Records ending after {cutoff} were not "
                     f"considered ({dropped} dropped), so the event itself cannot "
                     "appear here.", cutoff.isoformat(), "date", "measured"),
              F.item("national_register", "Every source is searched by country; a hit "
                     "is national unless its distance from the AOI says otherwise.",
                     None, "", "stated_by_source"),
              F.item("document_dates", "World Bank hits are dated by the "
                     "document, which can follow the event by weeks or months.",
                     None, "", "stated_by_source")]
    if status.get("gdacs", {}).get("page_full"):
        inputs.append(F.item("gdacs_page_full", f"GDACS returned a full page of "
                             f"{GDACS_PAGE}; older events may be missing.", GDACS_PAGE,
                             "events", "measured"))
    tool_items = [F.item(f"{k}_status", f"{k}: {v['status']}"
                         + (f" -- {v['problem']}" if v.get("problem") else ""),
                         v["status"], "", "measured")
                  for k, v in status.items() if v["status"] not in ("matched", "none")]
    sources = [
        F.source("gdacs", "Global Disaster Alert and Coordination System",
                 "EC JRC & UN OCHA", "https://www.gdacs.org/", "per event"),
        F.source("copernicus-ems", "Copernicus Emergency Management Service, Rapid "
                 "Mapping activations", "European Commission",
                 "https://mapping.emergency.copernicus.eu/activations/", "per activation"),
        F.source("unosat-hdx", "UNOSAT products on the Humanitarian Data Exchange",
                 "UNITAR-UNOSAT", "https://data.humdata.org/organization/unosat", "per product"),
        F.source("world-bank-documents", "World Bank Documents & Reports", "World Bank",
                 "https://documents.worldbank.org/", "per document"),
    ]
    return {
        "country_iso3": iso3, "country": name, "region": region or None,
        "search_cutoff": cutoff.isoformat(), "search_from": start.isoformat(),
        "events": kept, "count": len(kept), "dropped_at_or_after_cutoff": dropped,
        "references": sorted(references.values(), key=lambda r: r["cite"], reverse=True),
        "sources_status": status, "searched": searched,
        "note": "No casualty figures are kept (D-15). A source that was not "
                "searched is listed in sources_status, never read as 'nothing found'. "
                "Each event carries a short citation (publisher, year).",
        "uncertainty": F.blocks(inputs, tool_items),
        "sources": sources,
    }
