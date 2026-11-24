"""Cloud and wind AT THE ACQUISITIONS, not over the event window.

User decision 2026-11-22. The ranking's cloud index (W1) and wind index (W2)
were window means over every hour of the event -- every night included, and
every hour no product looked. They are now read at the moments the products
actually acquired:

* **Cloud (W1) at the OPTICAL passes** -- OPERA DSWx-HLS, MODIS MCDWD, VIIRS
  VCDWD -- because only an optical sensor is hurt by cloud (W1's
  susceptibility is 1 for optical, 0 for radar).
* **Wind (W2) at the RADAR passes** -- GFM and OPERA DSWx-S1 -- because only
  radar is hurt by wind roughening the water (W2's susceptibility is 1 for
  SAR, 0 for optical).
* Each pass's value is interpolated linearly between the two model hours
  either side of it; the index is the MEAN over the passes.
* The passes come from Module 1 (`data_availability`): the exact acquisition
  times for GFM, DSWx-S1 and DSWx-HLS; for MODIS and VIIRS, whose daily
  composites carry no time, the NOMINAL overpass -- Terra 10:30, Aqua 13:30,
  VIIRS 13:30 local SOLAR time -- on every day of the window. The values come
  from Module 2 (`weather_grid`): the hourly AOI-mean model cloud and 10 m wind.

**Guardrail: everything is converted to LOCAL time before it is matched or
counted** (user rule 2026-11-22). The pass times (UTC in Module 1), the model
hours (UTC in Module 2) and the nominal overpasses (local SOLAR time, which is
not civil time: at 80.05 E solar 10:30 is 10:39 in Colombo) are all turned
into aware datetimes in the event's IANA zone first; a pass belongs to the
window by its LOCAL date. The zone is Module 1's (`period.timezone`), and
Module 2's stated offset must agree with it at every hour used -- a missing
zone or a disagreement raises `LocalTimeError` rather than guessing. Every
time this module returns is local, with its offset.

A pass whose hours are missing is withheld and counted, never filled. With no
pass left, the index is missing (`NoPassWeather`), and the ranking that needs
it says so -- it does not fall back to the window mean.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OPTICAL = ("dswx_hls", "mcdwd", "vcdwd")
RADAR = ("gfm", "dswx_s1")
# Nominal overpass, local SOLAR hours (Module 1 uses the same).
NOMINAL_SOLAR_H = {"mcdwd": {"Terra": 10.5, "Aqua": 13.5}, "vcdwd": {"VIIRS": 13.5}}
# Two products cut from one Sentinel-1 pass (GFM and DSWx-S1) are one
# acquisition: passes this close are counted once.
SAME_PASS = timedelta(minutes=5)
RULE = ("cloud at the optical passes (OPERA DSWx-HLS, MODIS, VIIRS) and wind at the "
        "radar passes (GFM, OPERA DSWx-S1), each interpolated between the model hours "
        "either side and averaged over the passes; all times local")


class LocalTimeError(ValueError):
    """The local time zone is missing, or the two payloads disagree on it."""


class NoPassWeather(ValueError):
    """No acquisition in the window has weather data at its time."""


def _zone(availability: dict[str, Any]) -> ZoneInfo:
    name = ((availability.get("period") or {}).get("timezone") or "").strip()
    if not name:
        raise LocalTimeError(
            "Module 1 stored no local time zone, so its pass times cannot be put in "
            "local time; run the availability check again.")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise LocalTimeError(f"the time zone {name!r} is not in the tz database") from exc


def _utc(stamp: str) -> datetime:
    """'2025-11-26T00:25:15Z' or '2025-11-26T00:00Z' -> aware UTC."""
    text = stamp.strip().replace("Z", "+00:00")
    out = datetime.fromisoformat(text)
    if out.tzinfo is None:
        raise LocalTimeError(f"the time {stamp!r} carries no zone; refusing to guess one")
    return out.astimezone(timezone.utc)


def local_hours(weather: dict[str, Any], zone: ZoneInfo,
                notes: list[str] | None = None) -> list[datetime]:
    """Module 2's hourly stamps as LOCAL aware datetimes.

    The stamps must be stored in UTC and say so (`meta.storage_tz` "UTC",
    each stamp ending in Z) -- then the conversion to the zone is exact. A
    store in any other or unstated zone is refused. Module 2's
    `local_utc_offset_h` is only a DISPLAY setting (default 0); when it
    disagrees with the zone it is noted, not used.
    """
    meta = weather.get("meta") or {}
    stored = str(meta.get("storage_tz") or "").upper()
    if stored != "UTC":
        raise LocalTimeError(
            f"Module 2's hours are stored in {stored or 'an unstated zone'}, not UTC; "
            "they cannot be put in local time without guessing.")
    stamps = (weather.get("hourly") or {}).get("time") or []
    out = [_utc(s).astimezone(zone) for s in stamps]
    shown = meta.get("local_utc_offset_h")
    if shown is not None and out and notes is not None:
        offsets = sorted({t.utcoffset().total_seconds() / 3600.0 for t in out})
        if any(abs(o - float(shown)) > 1e-6 for o in offsets):
            notes.append(f"Module 2 displays local times at UTC{float(shown):+g} h, but "
                         f"{zone.key} is UTC{offsets[0]:+g} h; its stored hours are UTC, so "
                         "the matching here is unaffected.")
    return out


def passes(availability: dict[str, Any], zone: ZoneInfo,
           centre_lon: float) -> dict[str, list[dict[str, Any]]]:
    """Every acquisition in the window, per product, in LOCAL time.

    Exact passes from their UTC stamps; MODIS / VIIRS at the nominal overpass
    on every local day of the window. A pass is in the window by its LOCAL date.
    """
    days = [date.fromisoformat(d) for d in (availability.get("period") or {}).get("days") or []]
    window = set(days)
    out: dict[str, list[dict[str, Any]]] = {}
    for product in availability.get("products") or []:
        pid = product.get("id")
        found: list[dict[str, Any]] = []
        if pid in NOMINAL_SOLAR_H:
            for day in days:
                # Local solar midnight is UTC midnight minus longitude / 15 h.
                solar_midnight = (datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
                                  - timedelta(hours=centre_lon / 15.0))
                for sensor, solar_h in NOMINAL_SOLAR_H[pid].items():
                    at = (solar_midnight + timedelta(hours=solar_h)).astimezone(zone)
                    found.append({"product": pid, "local": at, "how": f"nominal {sensor} "
                                  f"overpass, {solar_h:g} h local solar time"})
        else:
            for record in (product.get("days") or {}).values():
                for acq in record.get("acquisitions") or []:
                    if acq.get("utc"):
                        found.append({"product": pid, "local": _utc(acq["utc"]).astimezone(zone),
                                      "how": "acquisition time from the catalogue"})
        out[pid] = sorted((p for p in found if p["local"].date() in window),
                          key=lambda p: p["local"])
    return out


def _value_at(at: datetime, hours: list[datetime], series: list[Any]) -> float | None:
    """Linear between the two LOCAL hours around `at`; None on a gap."""
    for i in range(len(hours) - 1):
        if hours[i] <= at <= hours[i + 1]:
            a, b = (series[i] if i < len(series) else None,
                    series[i + 1] if i + 1 < len(series) else None)
            if a is None or b is None:
                return None
            span = (hours[i + 1] - hours[i]).total_seconds()
            frac = (at - hours[i]).total_seconds() / span if span else 0.0
            return float(a) + (float(b) - float(a)) * frac
    return None


def _one_per_pass(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Passes within SAME_PASS of each other are one acquisition."""
    kept: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda p: p["local"]):
        if kept and item["local"] - kept[-1]["local"] <= SAME_PASS:
            kept[-1]["products"].append(item["product"])
            continue
        kept.append({**item, "products": [item["product"]]})
    return kept


def _index(kind: str, products: tuple[str, ...], var: str, unit: str,
           by_product: dict[str, list[dict[str, Any]]], hours: list[datetime],
           weather: dict[str, Any]) -> dict[str, Any]:
    series = (((weather.get("hourly") or {}).get("vars") or {}).get(var) or {}).get("mean") or []
    chosen = _one_per_pass([p for pid in products for p in by_product.get(pid, [])])
    rows, withheld = [], []
    for p in chosen:
        value = _value_at(p["local"], hours, series)
        row = {"local": p["local"].isoformat(timespec="minutes"),
               "products": p["products"], "how": p["how"],
               "value": None if value is None else round(value, 3)}
        (rows if value is not None else withheld).append(row)
    if not rows:
        raise NoPassWeather(
            f"no {kind} pass in the window has weather data at its time "
            f"({len(withheld)} pass(es) fall on missing hours, {len(chosen)} in all); "
            f"the {kind} index is not known and is not replaced by the window mean.")
    mean = sum(r["value"] for r in rows) / len(rows)
    return {"value": round(mean, 4), "unit": unit, "products": list(products),
            "passes": len(rows), "withheld": len(withheld),
            "list": rows, "withheld_list": withheld,
            "from": f"weather_grid.hourly.vars.{var}.mean at data_availability passes"}


def pass_conditions(weather: dict[str, Any], availability: dict[str, Any]) -> dict[str, Any]:
    """Cloud at the optical passes and wind at the radar passes, local time.

    Returns {"timezone", "rule", "cloud": {...}, "wind": {...}}; each index
    carries its value, the passes used (local time, products, value), and the
    passes withheld for missing hours. Raises LocalTimeError when local time
    cannot be established, NoPassWeather when an index has no pass with data.
    """
    zone = _zone(availability)
    notes: list[str] = []
    hours = local_hours(weather, zone, notes)
    bbox = ((availability.get("aoi") or {}).get("bbox")
            or (weather.get("meta") or {}).get("bbox"))
    if not bbox or len(bbox) != 4:
        raise LocalTimeError("no AOI box to place the nominal MODIS / VIIRS overpass")
    by_product = passes(availability, zone, (float(bbox[0]) + float(bbox[2])) / 2)
    return {
        "timezone": zone.key,
        "rule": RULE,
        "cloud": _index("optical", OPTICAL, "cloud_cover_model", "%", by_product, hours, weather),
        "wind": _index("radar", RADAR, "wind_speed_10m", "m/s", by_product, hours, weather),
        "notes": notes,
    }
