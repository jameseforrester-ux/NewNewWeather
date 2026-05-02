"""City registry — all 35 cities the bot tracks.

Resolution rules verified against multiple Polymarket events per city:
  - Source: Wunderground, "all times on this day" window
  - Precision: whole degree (F for US, C for international)
  - Bucket width: 2°F (US) or 1°C (international)
  - Slug: highest-temperature-in-{slug}-on-{month}-{day}-{year}

The `ai_trap` field flags cities where Polymarket's auto-generated AI summaries
reference a DIFFERENT station than the actual resolution station — an exploitable
edge confirmed in spot-checks.

CRITICAL TZ NOTE:
Each market refers to the calendar day in the STATION's local timezone, not UTC
and not the user's TZ. The bot resolves dates per-city. See timeutil.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class City:
    name: str               # display name
    slug: str               # Polymarket slug token (lowercase, hyphenated)
    station_code: str       # ICAO or other identifier
    station_name: str
    lat: float
    lon: float
    tz: ZoneInfo
    wu_path: str            # path on Wunderground OR full URL for non-WU sources
    unit: str               # "F" or "C"
    bucket_width: int       # 2 for °F, 1 for °C
    resolution_source: str = "wunderground"   # "wunderground" | "hko" | ...
    precision: float = 1.0  # whole-degree by default; 0.1 for HKO
    ai_trap: str | None = None

    @property
    def source_url(self) -> str:
        if self.resolution_source == "wunderground":
            return f"https://www.wunderground.com/history/daily{self.wu_path}"
        return self.wu_path  # non-WU: wu_path holds full URL

    @property
    def source_label(self) -> str:
        return {
            "wunderground": "Wunderground",
            "hko":          "HK Observatory (Daily Extract)",
        }.get(self.resolution_source, self.resolution_source)


# ---------------------------------------------------------------------------
# US cities — Fahrenheit, 2°F bucket width
# ---------------------------------------------------------------------------
US_CITIES: list[City] = [
    City("New York City", "nyc", "KLGA", "LaGuardia Airport",
         40.7772, -73.8726, ZoneInfo("America/New_York"),
         "/us/ny/new-york-city/KLGA", "F", 2,
         ai_trap="KNYC (Central Park)"),
    City("Chicago", "chicago", "KORD", "O'Hare International",
         41.9786, -87.9047, ZoneInfo("America/Chicago"),
         "/us/il/chicago/KORD", "F", 2),
    City("Los Angeles", "los-angeles", "KLAX", "Los Angeles International",
         33.9416, -118.4085, ZoneInfo("America/Los_Angeles"),
         "/us/ca/los-angeles/KLAX", "F", 2),
    City("Miami", "miami", "KMIA", "Miami International",
         25.7959, -80.2870, ZoneInfo("America/New_York"),
         "/us/fl/miami/KMIA", "F", 2),
    City("Denver", "denver", "KBKF", "Buckley Space Force Base",
         39.7017, -104.7517, ZoneInfo("America/Denver"),
         "/us/co/aurora/KBKF", "F", 2,
         ai_trap="KDEN (Denver Intl)"),
    City("Atlanta", "atlanta", "KATL", "Hartsfield-Jackson Atlanta International",
         33.6407, -84.4277, ZoneInfo("America/New_York"),
         "/us/ga/atlanta/KATL", "F", 2),
    City("Seattle", "seattle", "KSEA", "Seattle-Tacoma International",
         47.4502, -122.3088, ZoneInfo("America/Los_Angeles"),
         "/us/wa/seattle/KSEA", "F", 2),
    City("Houston", "houston", "KHOU", "William P. Hobby Airport",
         29.6454, -95.2789, ZoneInfo("America/Chicago"),
         "/us/tx/houston/KHOU", "F", 2),
    City("Austin", "austin", "KAUS", "Austin-Bergstrom International",
         30.1945, -97.6699, ZoneInfo("America/Chicago"),
         "/us/tx/austin/KAUS", "F", 2),
]

# ---------------------------------------------------------------------------
# International cities — Celsius, 1°C bucket width
# ---------------------------------------------------------------------------
INTL_CITIES: list[City] = [
    City("Seoul", "seoul", "RKSI", "Incheon International",
         37.4602, 126.4407, ZoneInfo("Asia/Seoul"),
         "/kr/incheon/RKSI", "C", 1),
    City("Busan", "busan", "RKPK", "Gimhae International",
         35.1795, 128.9382, ZoneInfo("Asia/Seoul"),
         "/kr/busan/RKPK", "C", 1),
    City("Tokyo", "tokyo", "RJTT", "Tokyo Haneda Airport",
         35.5494, 139.7798, ZoneInfo("Asia/Tokyo"),
         "/jp/tokyo/RJTT", "C", 1),
    City("Hong Kong", "hong-kong", "HKO", "Hong Kong Observatory (King's Park)",
         22.3027, 114.1722, ZoneInfo("Asia/Hong_Kong"),
         "https://www.weather.gov.hk/en/cis/climat.htm", "C", 1,
         resolution_source="hko", precision=0.1),
    City("Singapore", "singapore", "WSSS", "Singapore Changi Airport",
         1.3644, 103.9915, ZoneInfo("Asia/Singapore"),
         "/sg/singapore/WSSS", "C", 1),
    City("Shanghai", "shanghai", "ZSPD", "Shanghai Pudong International",
         31.1443, 121.8083, ZoneInfo("Asia/Shanghai"),
         "/cn/shanghai/ZSPD", "C", 1),
    City("Beijing", "beijing", "ZBAA", "Beijing Capital International",
         40.0801, 116.5846, ZoneInfo("Asia/Shanghai"),
         "/cn/beijing/ZBAA", "C", 1),
    City("Shenzhen", "shenzhen", "ZGSZ", "Shenzhen Bao'an International",
         22.6393, 113.8108, ZoneInfo("Asia/Shanghai"),
         "/cn/shenzhen/ZGSZ", "C", 1),
    City("Guangzhou", "guangzhou", "ZGGG", "Guangzhou Baiyun International",
         23.3924, 113.2988, ZoneInfo("Asia/Shanghai"),
         "/cn/guangzhou/ZGGG", "C", 1),
    City("Wuhan", "wuhan", "ZHHH", "Wuhan Tianhe International",
         30.7838, 114.2081, ZoneInfo("Asia/Shanghai"),
         "/cn/wuhan/ZHHH", "C", 1),
    City("Qingdao", "qingdao", "ZSQD", "Qingdao Jiaodong International",
         36.3616, 120.0855, ZoneInfo("Asia/Shanghai"),
         "/cn/qingdao/ZSQD", "C", 1),
    City("Taipei", "taipei", "RCSS", "Taipei Songshan Airport",
         25.0697, 121.5519, ZoneInfo("Asia/Taipei"),
         "/tw/taipei/RCSS", "C", 1),
    City("Manila", "manila", "RPLL", "Ninoy Aquino International",
         14.5086, 121.0194, ZoneInfo("Asia/Manila"),
         "/ph/manila/RPLL", "C", 1),
    City("Jakarta", "jakarta", "WIHH", "Halim Perdanakusuma International",
         -6.2664, 106.8908, ZoneInfo("Asia/Jakarta"),
         "/id/jakarta/WIHH", "C", 1),

    # Europe
    City("London", "london", "EGLC", "London City Airport",
         51.5053, 0.0553, ZoneInfo("Europe/London"),
         "/gb/london/EGLC", "C", 1),
    City("Paris", "paris", "LFPB", "Paris-Le Bourget Airport",
         48.9694, 2.4414, ZoneInfo("Europe/Paris"),
         "/fr/paris/LFPB", "C", 1),
    City("Madrid", "madrid", "LEMD", "Adolfo Suárez Madrid-Barajas",
         40.4936, -3.5668, ZoneInfo("Europe/Madrid"),
         "/es/madrid/LEMD", "C", 1),
    City("Warsaw", "warsaw", "EPWA", "Warsaw Chopin Airport",
         52.1657, 20.9671, ZoneInfo("Europe/Warsaw"),
         "/pl/warsaw/EPWA", "C", 1),
    City("Helsinki", "helsinki", "EFHK", "Helsinki-Vantaa Airport",
         60.3172, 24.9633, ZoneInfo("Europe/Helsinki"),
         "/fi/helsinki/EFHK", "C", 1),
    City("Moscow", "moscow", "UUWW", "Vnukovo Airport (NOAA)",
         55.5915, 37.2615, ZoneInfo("Europe/Moscow"),
         "/ru/moscow/UUWW", "C", 1),
    City("Ankara", "ankara", "LTAC", "Esenboğa International",
         40.1281, 32.9951, ZoneInfo("Europe/Istanbul"),
         "/tr/ankara/LTAC", "C", 1),

    # Middle East / Africa
    City("Tel Aviv", "tel-aviv", "LLBG", "Ben Gurion International",
         32.0114, 34.8867, ZoneInfo("Asia/Jerusalem"),
         "/il/tel-aviv/LLBG", "C", 1),
    City("Cape Town", "cape-town", "FACT", "Cape Town International",
         -33.9648, 18.6017, ZoneInfo("Africa/Johannesburg"),
         "/za/cape-town/FACT", "C", 1),

    # Oceania
    City("Wellington", "wellington", "NZWN", "Wellington International",
         -41.3272, 174.8053, ZoneInfo("Pacific/Auckland"),
         "/nz/wellington/NZWN", "C", 1),

    # Latin America
    City("Buenos Aires", "buenos-aires", "SAEZ", "Ministro Pistarini International",
         -34.8222, -58.5358, ZoneInfo("America/Argentina/Buenos_Aires"),
         "/ar/buenos-aires/SAEZ", "C", 1),
    City("São Paulo", "sao-paulo", "SBGR", "São Paulo-Guarulhos International",
         -23.4356, -46.4731, ZoneInfo("America/Sao_Paulo"),
         "/br/sao-paulo/SBGR", "C", 1),
    City("Panama City", "panama-city", "MPMG", "Marcos A. Gelabert Airport",
         8.9733, -79.5556, ZoneInfo("America/Panama"),
         "/pa/panama-city/MPMG", "C", 1),
]

ALL_CITIES: list[City] = US_CITIES + INTL_CITIES
CITIES: dict[str, City] = {c.slug: c for c in ALL_CITIES}


def get_city(name_or_slug: str) -> City:
    key = name_or_slug.lower().strip().replace(" ", "-")
    if key in CITIES:
        return CITIES[key]
    for c in ALL_CITIES:
        if c.name.lower() == name_or_slug.lower().strip():
            return c
    raise KeyError(f"Unknown city {name_or_slug!r}. Try /cities.")
