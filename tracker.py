"""Position tracker.

Stores user-tracked markets in a JSON file and refines them on each update
checkpoint (3-day → 1-day → 12h → resolution).

Each tracked entry stores:
  - city slug + target date
  - confidence at each checkpoint (history)
  - recommended action for the latest update (ADD / TRIM / HOLD / EXIT)

The file is intentionally tiny and human-editable. Swap to SQLite if you need
multi-user durability or concurrent writes.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import config
import strategy
from cities import get_city
from strategy import Recommendation


_LOCK = threading.Lock()


@dataclass
class Checkpoint:
    timestamp: str            # ISO UTC
    hours_to_resolution: float
    consensus: float | None
    confidence: float
    p_any_wins: float
    p_primary_wins: float
    sizing_label: str
    sizing_per_position: float
    action: str               # ADD / TRIM / HOLD / EXIT / INITIAL


@dataclass
class TrackedMarket:
    city_slug: str
    target_date: str          # ISO date, station-local
    slug: str
    initial_confidence: float
    checkpoints: list[Checkpoint] = field(default_factory=list)
    closed: bool = False
    closed_reason: str | None = None

    @property
    def latest(self) -> Checkpoint | None:
        return self.checkpoints[-1] if self.checkpoints else None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _path() -> Path:
    return Path(config.TRACKER_PATH)


def _load_raw() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {"markets": {}}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"markets": {}}


def _save_raw(data: dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, p)


def _key(city_slug: str, target: date | str) -> str:
    if isinstance(target, date):
        target = target.isoformat()
    return f"{city_slug}:{target}"


def _to_dict(m: TrackedMarket) -> dict:
    return {
        "city_slug": m.city_slug,
        "target_date": m.target_date,
        "slug": m.slug,
        "initial_confidence": m.initial_confidence,
        "checkpoints": [asdict(c) for c in m.checkpoints],
        "closed": m.closed,
        "closed_reason": m.closed_reason,
    }


def _from_dict(d: dict) -> TrackedMarket:
    return TrackedMarket(
        city_slug=d["city_slug"],
        target_date=d["target_date"],
        slug=d["slug"],
        initial_confidence=d["initial_confidence"],
        checkpoints=[Checkpoint(**c) for c in d.get("checkpoints", [])],
        closed=d.get("closed", False),
        closed_reason=d.get("closed_reason"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def list_tracked(include_closed: bool = False) -> list[TrackedMarket]:
    with _LOCK:
        data = _load_raw()
    out = [_from_dict(d) for d in data.get("markets", {}).values()]
    if not include_closed:
        out = [m for m in out if not m.closed]
    out.sort(key=lambda m: m.target_date)
    return out


def get(city_slug: str, target: date | str) -> TrackedMarket | None:
    with _LOCK:
        data = _load_raw()
    raw = data.get("markets", {}).get(_key(city_slug, target))
    return _from_dict(raw) if raw else None


def add_or_update(rec: Recommendation, hours_to_resolution: float, action: str = "INITIAL") -> TrackedMarket:
    """Add a tracked market or append a new checkpoint to an existing one."""
    city = rec.event.city
    target = rec.event.target_date
    cp = Checkpoint(
        timestamp=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        hours_to_resolution=round(hours_to_resolution, 2),
        consensus=rec.consensus,
        confidence=rec.confidence,
        p_any_wins=rec.p_any_wins,
        p_primary_wins=rec.p_primary_wins,
        sizing_label=rec.sizing_label,
        sizing_per_position=rec.sizing_per_position,
        action=action,
    )

    with _LOCK:
        data = _load_raw()
        markets = data.setdefault("markets", {})
        k = _key(city.slug, target)
        if k in markets:
            tm = _from_dict(markets[k])
            # Determine refinement action by comparing to previous checkpoint
            prev = tm.latest
            if prev is not None and action == "INITIAL":
                cp.action = config.refinement_action(prev.confidence, rec.confidence)
            tm.checkpoints.append(cp)
        else:
            tm = TrackedMarket(
                city_slug=city.slug,
                target_date=target.isoformat(),
                slug=rec.event.slug,
                initial_confidence=rec.confidence,
                checkpoints=[cp],
            )
        markets[k] = _to_dict(tm)
        _save_raw(data)
    return tm


def close(city_slug: str, target: date | str, reason: str) -> bool:
    with _LOCK:
        data = _load_raw()
        k = _key(city_slug, target)
        if k not in data.get("markets", {}):
            return False
        data["markets"][k]["closed"] = True
        data["markets"][k]["closed_reason"] = reason
        _save_raw(data)
    return True


def remove(city_slug: str, target: date | str) -> bool:
    with _LOCK:
        data = _load_raw()
        k = _key(city_slug, target)
        if k not in data.get("markets", {}):
            return False
        del data["markets"][k]
        _save_raw(data)
    return True
