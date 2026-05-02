"""Timezone-aware date utilities.

CORE PRINCIPLE
--------------
A Polymarket weather market refers to a calendar day in the *station's* local
timezone — NOT UTC, and NOT the user's timezone.

When the user is in Denver at 23:30 on May 1, the situation across cities is:

    Denver       (MT, UTC-6) → still May 1            → today's market live
    NYC          (ET, UTC-4) → already 01:30 May 2    → May 1 resolved, May 2 live
    London       (UTC+1)     → already 06:30 May 2    → May 1 resolved hours ago
    Tokyo        (UTC+9)     → already 14:30 May 2    → May 2 nearly done!
    Wellington   (UTC+12)    → already 17:30 May 2    → May 2 close to resolution

If the bot pulls "May 1 Tokyo" at this moment it gets a market that resolved
~14 hours ago. The bot must always operate on the station's local date.

Functions
---------
- station_now(city)      → current datetime in station's local TZ
- station_today(city)    → today's date in station's local TZ
- target_date(city, n)   → date n days from now in station's local TZ
- hours_to_resolution(city, target) → hours from NOW (UTC) until 23:59 local
                                       on the target date
- format_relative(city, target) → "today", "tomorrow", "in 3 days", etc.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from cities import City


def station_now(city: City) -> datetime:
    """Current datetime in the station's local timezone."""
    return datetime.now(tz=city.tz)


def station_today(city: City) -> date:
    """Today's calendar date in the station's local timezone."""
    return station_now(city).date()


def target_date(city: City, days_ahead: int) -> date:
    """Calendar date `days_ahead` days from now in the station's local TZ."""
    return station_today(city) + timedelta(days=days_ahead)


def hours_to_resolution(city: City, target: date) -> float:
    """Hours from NOW (real wall-clock UTC) until 23:59:59 local on target date.

    A negative value means the market has already resolved (target is past).
    Used to gate which model is the primary signal:
      > 72h    → ECMWF medium-range only
      24-72h   → ECMWF + AIFS + GraphCast (3-day-out window)
      0-24h    → HRRR primary (US) or ECMWF short-range (intl)
      < 0h     → resolved, do not trade
    """
    end_local = datetime.combine(target, time(23, 59, 59), tzinfo=city.tz)
    delta = end_local - datetime.now(tz=timezone.utc)
    return delta.total_seconds() / 3600.0


def format_relative(city: City, target: date) -> str:
    """Human-readable distance from station_today(city) to target."""
    today = station_today(city)
    diff = (target - today).days
    if diff < 0:
        return f"{abs(diff)}d ago (resolved)"
    if diff == 0:
        return "today"
    if diff == 1:
        return "tomorrow"
    return f"in {diff}d"


def is_market_live(city: City, target: date) -> bool:
    """True if the market for `target` in `city` has not yet resolved."""
    return hours_to_resolution(city, target) > 0


def hrrr_in_range(city: City, target: date) -> bool:
    """HRRR (US) covers ~48h. Bot only trusts HRRR forecasts inside this window."""
    if city.unit != "F":
        return False  # HRRR is US-only
    return 0 < hours_to_resolution(city, target) <= 48
