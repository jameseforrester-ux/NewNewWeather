"""Strategy constants and tunable parameters."""

from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# Polymarket Gamma API (no key required)
# ---------------------------------------------------------------------------
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
SLUG_TEMPLATE = "highest-temperature-in-{city}-on-{month}-{day}-{year}"

# ---------------------------------------------------------------------------
# Open-Meteo endpoints (no key required)
# ---------------------------------------------------------------------------
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"

MODELS_DETERMINISTIC = [
    "ecmwf_ifs025",          # ECMWF IFS deterministic 0.25°
    "ecmwf_aifs025_single",  # ECMWF AIFS (AI deterministic)
    "gfs_graphcast025",      # DeepMind GraphCast
    "gfs_global",            # NOAA GFS 0.25°
]
MODEL_HRRR = "gfs_hrrr"      # NOAA HRRR (US only, 0-48h)
MODEL_ENS = "ecmwf_ifs025"   # ECMWF IFS ENS 51 members

# Down-weight GFS for Denver (known cold-air-damming weakness on frontal days)
DOWNWEIGHT_GFS_FOR = {"denver"}

# ---------------------------------------------------------------------------
# Strategy thresholds (from methodology)
# ---------------------------------------------------------------------------
NUM_BUCKETS = 3
ENTRY_PRICE_LOW = 0.25
ENTRY_PRICE_HIGH = 0.40
MIN_AGREEMENT_PROB = 0.60       # P(any of 3 buckets wins) per ENS

# Stop-loss / take-profit by entry band (Two-Phase strategy)
SL_LOW_BAND_FRAC = 0.50         # entry 25-30¢ → SL at 50% below
SL_HIGH_BAND_FRAC = 0.40        # entry 31-40¢ → SL at 40% below
TP_ADJ_LOW_BAND = (0.44, 0.48)  # adjacent TP firing range, low entry band
TP_ADJ_HIGH_BAND = (0.52, 0.58) # adjacent TP firing range, high entry band

# When does the "low collapse" event typically fire
LOW_COLLAPSE_HOURS_OUT = 24

# ---------------------------------------------------------------------------
# Confidence scoring weights (for the cross-city scanner)
#   confidence = w_p * P(any wins)
#              + w_m * model_agreement
#              + w_s * (1 - normalized_ENS_stdev)
#              + w_e * entry_window_score
# All four components are in [0, 1]; final confidence is in [0, 1].
# ---------------------------------------------------------------------------
W_P_ANY_WINS = 0.35
W_MODEL_AGREE = 0.25
W_ENS_TIGHTNESS = 0.20
W_ENTRY_WINDOW = 0.20

# ---------------------------------------------------------------------------
# Position sizing tiers (per bucket position; 3 buckets = 3x this per market)
# Base unit configurable via env var POSITION_UNIT (default $1)
# ---------------------------------------------------------------------------
POSITION_UNIT = float(os.getenv("POSITION_UNIT", "1.0"))

SIZING_TIERS = [
    # (min_confidence, multiplier, label)
    (0.90, 2.0, "MAX"),
    (0.80, 1.5, "STRONG"),
    (0.70, 1.0, "BASE"),
    (0.60, 0.5, "PROBE"),
    (0.00, 0.0, "SKIP"),
]


def position_size(confidence: float) -> tuple[float, str]:
    for thresh, mult, label in SIZING_TIERS:
        if confidence >= thresh:
            return round(POSITION_UNIT * mult, 2), label
    return 0.0, "SKIP"


# Refinement multipliers — applied when re-scoring at 1-day or 12h checkpoints.
# These adjust the recommended position SIZE based on how confidence has moved
# since the initial 3-day entry recommendation.
def refinement_action(prev_confidence: float, new_confidence: float) -> str:
    delta = new_confidence - prev_confidence
    if delta > 0.10:
        return "ADD"        # confidence rose meaningfully → top up
    if delta < -0.10:
        return "TRIM"       # confidence fell → reduce exposure
    if new_confidence < MIN_AGREEMENT_PROB:
        return "EXIT"       # below threshold entirely → exit
    return "HOLD"


# ---------------------------------------------------------------------------
# Telegram + scheduler
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Scheduled scan times (UTC). Open-Meteo refreshes after the major model runs.
# 04:00 UTC: after 00Z ECMWF/GFS runs ingest
# 16:00 UTC: after 12Z runs ingest
SCAN_HOURS_UTC = (4, 16)
HRRR_REFRESH_MIN = 60   # HRRR runs hourly inside its 48h window

# Tracker storage (simple JSON file; swap to SQLite if you need durability)
TRACKER_PATH = os.getenv("TRACKER_PATH", "tracker.json")
