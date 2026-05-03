"""Forecast engine — Open-Meteo + METAR."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

import httpx

import config
from cities import City
from metar import fetch_metar, MetarObservation   # ← added


@dataclass
class ModelForecast:
    name: str
    model_id: str
    daily_max: float | None
    in_range: bool


@dataclass
class EnsembleForecast:
    name: str
    model_id: str
    members: list[float]

    @property
    def median(self) -> float | None:
        return statistics.median(self.members) if self.members else None

    @property
    def stdev(self) -> float | None:
        if len(self.members) < 2:
            return None
        return statistics.stdev(self.members)

    def prob_in_range(self, low: float, high: float) -> float:
        if not self.members:
            return 0.0
        hits = sum(1 for m in self.members if low <= m <= high)
        return hits / len(self.members)


@dataclass
class ForecastBundle:
    target: date
    city: City
    deterministic: list[ModelForecast] = field(default_factory=list)
    ensemble: EnsembleForecast | None = None
    hrrr: ModelForecast | None = None
    metar: MetarObservation | None = None   # ← NEW


    def consensus(self) -> float | None:
        vals: list[float] = []
        for m in self.deterministic:
            if m.daily_max is None:
                continue
            if self.city.slug in config.DOWNWEIGHT_GFS_FOR and m.model_id == "gfs_global":
                continue
            vals.append(m.daily_max)
        if self.ensemble and self.ensemble.median is not None:
            vals.append(self.ensemble.median)
        if self.hrrr and self.hrrr.daily_max is not None:
            vals.append(self.hrrr.daily_max)
        return round(statistics.median(vals), 1) if vals else None


# ... (rest of _LABELS, _unit_param, _multi_model_max, _fetch_ensemble unchanged) ...

async def fetch_bundle(
    client: httpx.AsyncClient, city: City, target: date,
) -> ForecastBundle:
    bundle = ForecastBundle(target=target, city=city)

    # deterministic + HRRR + ensemble (your original code) ...
    det = await _multi_model_max(...)   # keep your existing calls
    # ... populate deterministic and hrrr ...

    bundle.ensemble = await _fetch_ensemble(client, city, target)

    # NEW: Real-time METAR
    bundle.metar = await fetch_metar(client, city.station_code)

    return bundle
