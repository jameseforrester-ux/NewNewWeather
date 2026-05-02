"""Polymarket Gamma API client.

Builds the per-(city, date) slug, fetches the event, and parses each
sub-market into a typed Bucket carrying its temperature range, YES price,
and condition ID. Unit-aware: handles °F (US, 2°-wide) and °C (intl, 1°-wide)
buckets uniformly.

Endpoint:
    GET https://gamma-api.polymarket.com/events?slug={slug}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

import httpx

import config
from cities import City


_MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

# Patterns (work for both °F and °C since label parsing is by digit, not unit)
_RANGE_RE    = re.compile(r"(-?\d+)\s*[-–]\s*(-?\d+)\s*°?\s*[FC]?", re.I)
_LOW_CAP_RE  = re.compile(r"(-?\d+)\s*°?\s*[FC]?\s+or\s+below", re.I)
_HIGH_CAP_RE = re.compile(r"(-?\d+)\s*°?\s*[FC]?\s+or\s+(higher|above|more)", re.I)
_SINGLE_RE   = re.compile(r"^\s*(-?\d+)\s*°?\s*[FC]\s*$", re.I)


@dataclass
class Bucket:
    label: str
    low: float          # inclusive (-inf for low-cap)
    high: float         # inclusive (+inf for high-cap)
    yes_price: float    # 0..1
    no_price: float
    condition_id: str
    question: str

    @property
    def midpoint(self) -> float:
        if self.low == float("-inf"):
            return self.high - 0.5
        if self.high == float("inf"):
            return self.low + 0.5
        return (self.low + self.high) / 2

    def contains(self, t: float) -> bool:
        return self.low <= t <= self.high


@dataclass
class Event:
    slug: str
    title: str
    city: City
    target_date: date
    buckets: list[Bucket]
    volume: float | None
    raw: dict


def build_slug(city: City, target: date) -> str:
    return config.SLUG_TEMPLATE.format(
        city=city.slug,
        month=_MONTHS[target.month - 1],
        day=target.day,           # NOT zero-padded — verified across all sample slugs
        year=target.year,
    )


def _parse_label(question: str, group_title: str | None) -> tuple[float, float, str] | None:
    """Try the most specific text first, fall back to question text."""
    for text in (group_title, question):
        if not text:
            continue
        if (m := _SINGLE_RE.search(text.strip())):
            v = int(m.group(1))
            return float(v), float(v), f"{v}°"   # °C single-degree bucket
        if (m := _RANGE_RE.search(text)):
            lo, hi = int(m.group(1)), int(m.group(2))
            return float(lo), float(hi), f"{lo}-{hi}°"
        if (m := _LOW_CAP_RE.search(text)):
            cap = int(m.group(1))
            return float("-inf"), float(cap), f"{cap}° or below"
        if (m := _HIGH_CAP_RE.search(text)):
            cap = int(m.group(1))
            return float(cap), float("inf"), f"{cap}° or higher"
    return None


async def fetch_event(client: httpx.AsyncClient, city: City, target: date) -> Event | None:
    slug = build_slug(city, target)
    r = await client.get(
        f"{config.POLYMARKET_GAMMA}/events",
        params={"slug": slug},
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()
    if not payload:
        return None
    ev = payload[0] if isinstance(payload, list) else payload

    buckets: list[Bucket] = []
    for m in ev.get("markets", []):
        if m.get("closed") or m.get("archived"):
            continue
        question = m.get("question") or ""
        group_title = m.get("groupItemTitle")
        parsed = _parse_label(question, group_title)
        if not parsed:
            continue
        lo, hi, label = parsed

        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except json.JSONDecodeError:
                prices = None
        if not prices or len(prices) < 2:
            continue
        try:
            yes = float(prices[0]); no = float(prices[1])
        except (TypeError, ValueError):
            continue

        buckets.append(Bucket(
            label=label.replace("°", f"°{city.unit}"),
            low=lo, high=hi,
            yes_price=yes, no_price=no,
            condition_id=m.get("conditionId", ""),
            question=question,
        ))

    buckets.sort(key=lambda b: b.midpoint)
    return Event(
        slug=slug,
        title=ev.get("title") or slug,
        city=city,
        target_date=target,
        buckets=buckets,
        volume=float(ev["volume"]) if ev.get("volume") is not None else None,
        raw=ev,
    )
