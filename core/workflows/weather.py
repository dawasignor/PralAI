"""Historical weather ingestion from the Open-Meteo Archive API.

https://open-meteo.com/en/docs/historical-weather-api

One HTTP call fetches every variable; a small function then pulls one series
each out of the response. That keeps the extractors independently testable
without paying for five round trips.

Variables requested (order matters -- the SDK returns them by index, in the
order they were asked for, which is what `_HOURLY` and `_DAILY` pin down):

    cloud_cover          hourly  cloud_cover           Cloud cover total, %
    hourly_rain          hourly  rain                  Rain, mm
    wind_speed           hourly  wind_speed_10m        Wind speed at 10 m, km/h
    shortwave_radiation  hourly  shortwave_radiation   GHI, W/m2
    daily_rain           daily   rain_sum              Rain sum, mm

Soil moisture is ERA5-Land reanalysis, added because Sentinel-2 cannot measure
it (no thermal channel, no L-band radar):

    soil_moisture_surface    hourly  soil_moisture_0_to_7cm    m^3/m^3
    soil_moisture_root_zone  hourly  soil_moisture_7_to_28cm   m^3/m^3
"""

from __future__ import annotations

import math
from typing import Any

from . import retired

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Request order == response index order. Do not reorder without updating both.
# Append only -- inserting in the middle silently relabels every series after
# the insertion point.
_HOURLY = ["cloud_cover", "rain", "wind_speed_10m", "shortwave_radiation",
           "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm"]
_DAILY = ["rain_sum"]

_UNITS = {
    "cloud_cover": "%",
    "hourly_rain": "mm",
    "wind_speed": "km/h",
    "shortwave_radiation": "W/m^2",
    "daily_rain": "mm",
    "soil_moisture_surface": "m^3/m^3",
    "soil_moisture_root_zone": "m^3/m^3",
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
def _client():
    """Open-Meteo client with an on-disk cache and retry/backoff.

    Imported lazily so the rest of the repo runs without the weather stack
    installed, and so an ImportError names the missing package.
    """
    try:
        import openmeteo_requests
        import requests_cache
        from retry_requests import retry
    except ImportError as exc:                                # pragma: no cover
        raise RuntimeError(
            "weather workflows need: pip install openmeteo-requests "
            "requests-cache retry-requests pandas"
        ) from exc

    # Cache for an hour: archive data is immutable, so repeated runs over the
    # same window cost nothing and stay well inside the free rate limit.
    cache = requests_cache.CachedSession(".cache", expire_after=3600)
    return openmeteo_requests.Client(session=retry(cache, retries=5,
                                                   backoff_factor=0.2))


def _timestamps(block, utc_offset_seconds: int = 0) -> list[str]:
    """ISO-8601 timestamps for one hourly/daily block, in the AOI's local time.

    Rendered at the resolved offset, not in UTC. The API returns the UTC
    instant of each local period, so a Kerala day begins at 18:30 UTC the
    previous date -- printed as UTC that reads "2026-07-28", which is the day
    before the one it describes. Slicing a date off such a string silently
    reports the wrong day. At +05:30 the same instant prints as
    "2026-07-29T00:00:00+05:30", which cannot be misread.
    """
    from datetime import timedelta, timezone as dt_timezone

    import pandas as pd

    index = pd.date_range(
        start=pd.to_datetime(block.Time(), unit="s", utc=True),
        end=pd.to_datetime(block.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=block.Interval()),
        inclusive="left",          # TimeEnd is exclusive
    )
    if utc_offset_seconds:
        index = index.tz_convert(
            dt_timezone(timedelta(seconds=int(utc_offset_seconds))))
    return [t.isoformat() for t in index]


def _text(value) -> str | None:
    """Decode an SDK string field, which arrives as bytes."""
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def _values(block, index: int) -> list[float | None]:
    """Values for one variable, JSON-safe.

    Open-Meteo uses NaN for gaps; JSON has no NaN, so gaps become null rather
    than a number that silently lies about a missing observation.
    """
    raw = block.Variables(index).ValuesAsNumpy()
    return [None if math.isnan(v) else round(float(v), 4) for v in raw]


def _series(block, index: int, name: str,
            utc_offset_seconds: int = 0) -> dict[str, Any]:
    return {
        "time": _timestamps(block, utc_offset_seconds),
        "values": _values(block, index),
        "unit": _UNITS[name],
    }


# ---------------------------------------------------------------------------
# One function per variable
# ---------------------------------------------------------------------------
def cloud_cover(response) -> dict[str, Any]:
    """Total cloud cover, hourly (%)."""
    return _series(response.Hourly(), _HOURLY.index("cloud_cover"),
                   "cloud_cover", response.UtcOffsetSeconds())


def hourly_rain(response) -> dict[str, Any]:
    """Rain, hourly (mm)."""
    return _series(response.Hourly(), _HOURLY.index("rain"), "hourly_rain",
                   response.UtcOffsetSeconds())


def wind_speed(response) -> dict[str, Any]:
    """Wind speed at 10 m, hourly (km/h)."""
    return _series(response.Hourly(), _HOURLY.index("wind_speed_10m"),
                   "wind_speed", response.UtcOffsetSeconds())


def shortwave_radiation(response) -> dict[str, Any]:
    """Shortwave solar radiation / GHI, hourly (W/m^2)."""
    return _series(response.Hourly(), _HOURLY.index("shortwave_radiation"),
                   "shortwave_radiation", response.UtcOffsetSeconds())


def daily_rain(response) -> dict[str, Any]:
    """Rain sum, daily (mm)."""
    return _series(response.Daily(), _DAILY.index("rain_sum"), "daily_rain",
                   response.UtcOffsetSeconds())


def soil_moisture_surface(response) -> dict[str, Any]:
    """Volumetric soil water, 0-7 cm, hourly (m^3/m^3).

    This is genuine modelled soil moisture from ERA5-Land, not an optical
    proxy. Sentinel-2 cannot measure soil moisture -- it has neither a thermal
    channel nor an L-band radar -- so the physical quantity comes from here and
    Sentinel-2 contributes only the spatial pattern (NMDI). The trade is
    resolution: ERA5-Land is a ~9 km grid, so this is one value for the whole
    AOI, and it is unaffected by cloud.
    """
    return _series(response.Hourly(), _HOURLY.index("soil_moisture_0_to_7cm"),
                   "soil_moisture_surface", response.UtcOffsetSeconds())


def soil_moisture_root_zone(response) -> dict[str, Any]:
    """Volumetric soil water, 7-28 cm, hourly (m^3/m^3)."""
    return _series(response.Hourly(), _HOURLY.index("soil_moisture_7_to_28cm"),
                   "soil_moisture_root_zone", response.UtcOffsetSeconds())


_EXTRACTORS = {
    "cloud_cover": cloud_cover,
    "hourly_rain": hourly_rain,
    "wind_speed": wind_speed,
    "shortwave_radiation": shortwave_radiation,
    "daily_rain": daily_rain,
    "soil_moisture_surface": soil_moisture_surface,
    "soil_moisture_root_zone": soil_moisture_root_zone,
}


# ---------------------------------------------------------------------------
# The registered workflow
# ---------------------------------------------------------------------------
# RETIRED from the registry on 2026-10-25:
# `fetch_weather` (weather_grid.py) is the one weather tool. This file is kept
# until the user approves its deletion, and the function still imports for
# tests/test_weather_workflow.py. It names no model and labels ECMWF IFS data
# as ERA5-Land -- closed by this retirement, not fixed.
@retired("fetch_weather_data", "fetch_weather",
         "the point weather tool was replaced by the gridded one")
def fetch_weather_data(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    variables: list[str] | None = None,
    timezone: str = "auto",
) -> dict[str, Any]:
    """Fetch historical weather series for a point.

    Args:
        latitude, longitude: decimal degrees, e.g. 55.9533, -3.1883.
        start_date, end_date: inclusive, "YYYY-MM-DD" in the AOI's local time.
        variables: subset of the seven series; None means all of them.
        timezone: IANA name, or "auto" (the default) to resolve it from the
            coordinates. See the note below before overriding it.

    Returns a dict whose `series` holds each variable as its own list.

    **Times are local to the AOI.** `timezone="auto"` asks Open-Meteo to
    resolve the zone from the coordinates, so a date means the same thing to
    the workflow as it does to somebody standing in the catchment. This is not
    cosmetic: daily aggregation buckets on the local midnight, so the zone
    changes the numbers. Over Wayanad, 29 July 2026 returned 26.6 mm under
    Asia/Kolkata and 23.3 mm under GMT -- a 13% swing on the same day from the
    bucket boundary alone.

    Passing an explicit zone that is not the AOI's is almost always a mistake.
    The resolved zone comes back in `location.timezone`, so check it there
    rather than assuming the request was honoured.
    """
    wanted = list(variables) if variables else list(_EXTRACTORS)
    unknown = [v for v in wanted if v not in _EXTRACTORS]
    if unknown:
        raise ValueError(
            f"unknown variable(s) {unknown}; expected any of {sorted(_EXTRACTORS)}"
        )

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": _HOURLY,
        "daily": _DAILY,
        "timezone": timezone,
    }
    response = _client().weather_api(ARCHIVE_URL, params=params)[0]

    series = {name: _EXTRACTORS[name](response) for name in wanted}
    return {
        "location": {
            # Echo back what the API actually resolved -- it snaps to its grid,
            # so these are not always the coordinates that were requested.
            "latitude": round(response.Latitude(), 4),
            "longitude": round(response.Longitude(), 4),
            "elevation_m": response.Elevation(),
            # The RESOLVED zone, not the requested string -- echoing "auto"
            # back would tell a reader nothing about which day a daily value
            # covers. The SDK returns these as bytes.
            "timezone": _text(response.Timezone()) or timezone,
            "timezone_abbreviation": _text(response.TimezoneAbbreviation()),
            "timezone_requested": timezone,
            "utc_offset_seconds": response.UtcOffsetSeconds(),
        },
        "period": {"start_date": start_date, "end_date": end_date},
        "series": series,
        "counts": {name: len(s["values"]) for name, s in series.items()},
        "source": ARCHIVE_URL,
    }
