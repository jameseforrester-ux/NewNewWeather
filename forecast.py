"""Forecast engine — Open-Meteo client.

Pulls daily-max temperature forecasts in the unit each city's market resolves
in (°F for US, °C for international), so values flow straight into the
strategy without conversion.

Models requested:
  - ECMWF IFS deterministic (medium-range backbone)
  - ECMWF AIFS (AI deterministic)
  - DeepMind GraphCast
  - NOAA GFS (down-weighted for Denver via the strategy layer)
  - NOAA HRRR (US-only, 0-48h)
  - ECMWF IFS ENS — 51 ensemble members for probabilistic strike selection
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

import httpx

import config
from cities import City


@dataclass
class ModelForecast:
    name: str
    model_id: str
    daily_max: float | None
    in_range: bool

    def bucket_low(self, width: int) -> int | None:
        """Lower edge of the width-° bucket containing this forecast (no rounding bias)."""
        if self.daily_max is None:
            return None
        v = round(self.daily_max)
        if width == 1:
            return v                                # 1°C buckets: each integer is its own bucket
        # 2°F buckets: pair (even, odd) → label is even-low
        return v if v % 2 == 0 else v - 1


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

    def consensus(self) -> float | None:
        """Median of ECMWF det + AIFS + GraphCast + ENS median.

        Excludes GFS for Denver per the methodology (cold-air-damming weakness).
        Includes HRRR if it's in range.
        """
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


_LABELS = {
    "ecmwf_ifs025": "ECMWF IFS",
    "ecmwf_aifs025_single": "ECMWF AIFS",
    "gfs_graphcast025": "GraphCast",
    "gfs_global": "GFS",
    "gfs_hrrr": "HRRR",
}


def _unit_param(city: City) -> str:
    return "fahrenheit" if city.unit == "F" else "celsius"


async def _multi_model_max(
    client: httpx.AsyncClient,
    city: City,
    target: date,
    models: list[str],
    *,
    forecast_days: int,
    url: str = config.OPEN_METEO_FORECAST,
) -> dict[str, float | None]:
    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "daily": "temperature_2m_max",
        "temperature_unit": _unit_param(city),
        "timezone": str(city.tz),
        "forecast_days": forecast_days,
        "models": ",".join(models),
    }
    r = await client.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    out: dict[str, float | None] = {m: None for m in models}
    daily = data.get("daily", {})
    times: list[str] = daily.get("time", [])
    target_iso = target.isoformat()

    if len(models) == 1:
        m = models[0]
        maxes = daily.get("temperature_2m_max", [])
        for t, v in zip(times, maxes):
            if t == target_iso:
                out[m] = v
                break
        return out

    try:
        idx = times.index(target_iso)
    except ValueError:
        return out
    for m in models:
        arr = daily.get(f"temperature_2m_max_{m}")
        if arr and idx < len(arr):
            out[m] = arr[idx]
    return out


async def _fetch_ensemble(
    client: httpx.AsyncClient, city: City, target: date,
) -> EnsembleForecast | None:
    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "hourly": "temperature_2m",
        "temperature_unit": _unit_param(city),
        "timezone": str(city.tz),
        "forecast_days": 16,
        "models": config.MODEL_ENS,
    }
    try:
        r = await client.get(config.OPEN_METEO_ENSEMBLE, params=params, timeout=40)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    data = r.json()
    hourly = data.get("hourly", {})
    times: list[str] = hourly.get("time", [])
    if not times:
        return None

    target_prefix = target.isoformat()
    target_idxs = [i for i, t in enumerate(times) if t.startswith(target_prefix)]
    if not target_idxs:
        return None

    members: list[float] = []
    for key, series in hourly.items():
        if key == "time" or not key.startswith("temperature_2m") or not series:
            continue
        day_vals = [series[i] for i in target_idxs if series[i] is not None]
        if day_vals:
            members.append(round(max(day_vals), 1))

    return EnsembleForecast(name="ECMWF ENS (51m)", model_id=config.MODEL_ENS, members=members)


async def fetch_bundle(
    client: httpx.AsyncClient, city: City, target: date,
) -> ForecastBundle:
    bundle = ForecastBundle(target=target, city=city)

    det = await _multi_model_max(
        client, city, target, config.MODELS_DETERMINISTIC, forecast_days=16,
    )
    for mid in config.MODELS_DETERMINISTIC:
        v = det.get(mid)
        bundle.deterministic.append(ModelForecast(
            name=_LABELS.get(mid, mid),
            model_id=mid,
            daily_max=round(v, 1) if v is not None else None,
            in_range=v is not None,
        ))

    # HRRR — US only, 48h horizon
    if city.unit == "F":
        try:
            hrrr = await _multi_model_max(
                client, city, target, [config.MODEL_HRRR], forecast_days=2,
            )
            v = hrrr.get(config.MODEL_HRRR)
            bundle.hrrr = ModelForecast(
                name="HRRR", model_id=config.MODEL_HRRR,
                daily_max=round(v, 1) if v is not None else None,
                in_range=v is not None,
            )
        except httpx.HTTPError:
            bundle.hrrr = None

    bundle.ensemble = await _fetch_ensemble(client, city, target)
    return bundle
