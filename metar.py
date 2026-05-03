"""Real-time METAR/ASOS for resolution airports."""

import re
from dataclasses import dataclass
from datetime import datetime

import httpx


@dataclass
class MetarObservation:
    station: str
    temp_f: float | None
    temp_c: float | None
    obs_time: datetime | None
    raw: str

    @property
    def temp_str(self) -> str:
        if self.temp_f is not None:
            return f"{self.temp_f:.1f}°F / {self.temp_c:.1f}°C" if self.temp_c else f"{self.temp_f:.1f}°F"
        return "—"


async def fetch_metar(client: httpx.AsyncClient, station_code: str) -> MetarObservation | None:
    try:
        r = await client.get(
            "https://aviationweather.gov/api/data/metar",
            params={"ids": station_code, "format": "json", "hours": 3},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            return None

        latest = data[0] if isinstance(data, list) else data
        raw = latest.get("raw", "")

        # Parse temperature
        temp_match = re.search(r"(?:^|\s)(M?\d{2})/(?:M?\d{2})", raw)
        temp_f = temp_c = None
        if temp_match:
            t_str = temp_match.group(1)
            sign = -1 if t_str.startswith("M") else 1
            val = int(t_str.replace("M", ""))
            temp_c = sign * val
            temp_f = round(temp_c * 9/5 + 32, 1)

        obs_time = None
        if latest.get("time"):
            try:
                obs_time = datetime.fromisoformat(latest["time"].replace("Z", "+00:00"))
            except:
                pass

        return MetarObservation(
            station=station_code,
            temp_f=temp_f,
            temp_c=temp_c,
            obs_time=obs_time,
            raw=raw,
        )
    except Exception:
        return None
