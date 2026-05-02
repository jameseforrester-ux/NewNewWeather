"""Cross-city scanner.

Pulls forecasts + Polymarket events for every city across days_ahead horizon
(typically 1-3 days), builds Recommendations, returns a confidence-ranked list.

Critical: each city's `target_date` is computed in its OWN local timezone,
so the bot never queries a market that has already resolved on the other side
of the dateline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

import config
import polymarket
import forecast
import strategy
import timeutil
from cities import ALL_CITIES, City
from strategy import Recommendation


@dataclass
class ScanResult:
    rec: Recommendation
    hours_to_resolution: float

    @property
    def days_out(self) -> int:
        return max(0, int(self.hours_to_resolution // 24))


async def _scan_one(
    client: httpx.AsyncClient,
    city: City,
    days_ahead: int,
) -> ScanResult | None:
    """Build a Recommendation for `city` at its local-date offset of `days_ahead`."""
    target = timeutil.target_date(city, days_ahead)
    htr = timeutil.hours_to_resolution(city, target)
    if htr <= 0:
        return None  # already resolved in the station's TZ

    try:
        event = await polymarket.fetch_event(client, city, target)
    except httpx.HTTPError:
        return None
    if event is None or not event.buckets:
        return None

    try:
        bundle = await forecast.fetch_bundle(client, city, target)
    except httpx.HTTPError:
        return None

    rec = strategy.build_recommendation(event, bundle)
    return ScanResult(rec=rec, hours_to_resolution=htr)


async def scan_all(
    days_ahead_range: range = range(0, 4),  # today + 1d + 2d + 3d
    *,
    cities: list[City] | None = None,
    only_enterable: bool = False,
    concurrency: int = 8,
) -> list[ScanResult]:
    """Scan every city across `days_ahead_range`. Returns confidence-ranked list."""
    cities = cities or ALL_CITIES
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:

        async def task(c: City, d: int):
            async with sem:
                return await _scan_one(client, c, d)

        coros = [task(c, d) for c in cities for d in days_ahead_range]
        results = await asyncio.gather(*coros, return_exceptions=True)

    out: list[ScanResult] = []
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        if only_enterable and not r.rec.enter_signal:
            continue
        out.append(r)
    out.sort(key=lambda x: x.rec.confidence, reverse=True)
    return out


async def scan_top(n: int = 10, days_ahead_range: range = range(0, 4)) -> list[ScanResult]:
    """Convenience: top-N by confidence."""
    results = await scan_all(days_ahead_range=days_ahead_range, only_enterable=False)
    return results[:n]
