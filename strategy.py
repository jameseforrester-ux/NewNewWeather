"""Strategy engine.

Builds a Recommendation from (Polymarket Event + ForecastBundle):
  1. Pick 3 buckets centered on consensus
  2. Compute P(any wins), P(primary wins) from ECMWF ENS
  3. Compute confidence score from 4 signal components
  4. Tag entry signal if all gates pass
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import Enum

import config
from forecast import ForecastBundle, ModelForecast
from polymarket import Bucket, Event


class Role(str, Enum):
    PRIMARY = "primary"
    ADJ_LOW = "adj_low"
    ADJ_HIGH = "adj_high"


@dataclass
class Position:
    bucket: Bucket
    role: Role
    entry_price: float
    sl: float
    tp: float | None     # None for primary (hold to resolution)
    ens_prob: float


@dataclass
class Recommendation:
    event: Event
    bundle: ForecastBundle
    consensus: float | None
    positions: list[Position]
    p_any_wins: float
    p_primary_wins: float
    confidence: float
    components: dict[str, float]   # for explainability in the message card
    enter_signal: bool
    skip_reason: str | None = None
    sizing_label: str = "SKIP"
    sizing_per_position: float = 0.0


# ---------------------------------------------------------------------------
# Bucket selection
# ---------------------------------------------------------------------------
def select_three(event: Event, consensus: float) -> tuple[Bucket | None, Bucket, Bucket | None]:
    if not event.buckets:
        raise ValueError("event has no buckets")
    primary = next((b for b in event.buckets if b.contains(consensus)), None)
    if primary is None:
        primary = min(event.buckets, key=lambda b: abs(b.midpoint - consensus))
    idx = event.buckets.index(primary)
    adj_low = event.buckets[idx - 1] if idx > 0 else None
    adj_high = event.buckets[idx + 1] if idx < len(event.buckets) - 1 else None
    return adj_low, primary, adj_high


# ---------------------------------------------------------------------------
# SL/TP per Two-Phase methodology
# ---------------------------------------------------------------------------
def sl_tp_for(entry: float, role: Role) -> tuple[float, float | None]:
    if role is Role.PRIMARY:
        sl_frac = config.SL_HIGH_BAND_FRAC if entry >= 0.31 else config.SL_LOW_BAND_FRAC
        return round(entry * (1 - sl_frac), 2), None
    if entry <= 0.30:
        sl = round(entry * (1 - config.SL_LOW_BAND_FRAC), 2)
        tp = round(sum(config.TP_ADJ_LOW_BAND) / 2, 2)
    else:
        sl = round(entry * (1 - config.SL_HIGH_BAND_FRAC), 2)
        tp = round(sum(config.TP_ADJ_HIGH_BAND) / 2, 2)
    return sl, tp


# ---------------------------------------------------------------------------
# Confidence components
# ---------------------------------------------------------------------------
def _model_agreement(bundle: ForecastBundle, primary: Bucket) -> float:
    """Fraction of available deterministic models whose forecast falls in primary."""
    pool: list[ModelForecast] = [m for m in bundle.deterministic if m.daily_max is not None]
    if bundle.city.slug in config.DOWNWEIGHT_GFS_FOR:
        pool = [m for m in pool if m.model_id != "gfs_global"]
    if bundle.hrrr and bundle.hrrr.daily_max is not None:
        pool.append(bundle.hrrr)
    if not pool:
        return 0.0
    hits = sum(1 for m in pool if primary.contains(m.daily_max))
    return hits / len(pool)


def _ens_tightness(bundle: ForecastBundle) -> float:
    """1 - normalized ENS stdev (higher = tighter ensemble)."""
    if not bundle.ensemble or bundle.ensemble.stdev is None:
        return 0.0
    width = bundle.city.bucket_width
    # Normalize by 3 buckets: stdev of 3 buckets-wide → 0; stdev of 0 → 1
    normalized = min(bundle.ensemble.stdev / (3 * width), 1.0)
    return 1.0 - normalized


def _entry_window_score(buckets: list[Bucket]) -> float:
    """How well the 3-bucket prices sit inside the 25-40¢ entry window."""
    if not buckets:
        return 0.0
    in_window = 0
    for b in buckets:
        if config.ENTRY_PRICE_LOW <= b.yes_price <= config.ENTRY_PRICE_HIGH:
            in_window += 1
    return in_window / len(buckets)


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------
def build_recommendation(event: Event, bundle: ForecastBundle) -> Recommendation:
    consensus = bundle.consensus()

    if consensus is None:
        return Recommendation(
            event=event, bundle=bundle, consensus=None, positions=[],
            p_any_wins=0.0, p_primary_wins=0.0,
            confidence=0.0, components={},
            enter_signal=False, skip_reason="no model forecast in range",
        )

    if not event.buckets:
        return Recommendation(
            event=event, bundle=bundle, consensus=consensus, positions=[],
            p_any_wins=0.0, p_primary_wins=0.0,
            confidence=0.0, components={},
            enter_signal=False, skip_reason="no buckets parsed from event",
        )

    adj_low, primary, adj_high = select_three(event, consensus)
    selected = [b for b in (adj_low, primary, adj_high) if b is not None]

    # ENS-derived bucket probabilities
    if bundle.ensemble and bundle.ensemble.members:
        p_primary = bundle.ensemble.prob_in_range(primary.low, primary.high)
        p_any = sum(bundle.ensemble.prob_in_range(b.low, b.high) for b in selected)
    else:
        # Fallback: use model agreement as a soft probability
        p_primary = _model_agreement(bundle, primary)
        p_any = min(1.0, p_primary * 1.3)

    # Confidence components
    comp_p_any = min(p_any, 1.0)
    comp_model = _model_agreement(bundle, primary)
    comp_tight = _ens_tightness(bundle)
    comp_entry = _entry_window_score(selected)

    confidence = (
        config.W_P_ANY_WINS    * comp_p_any +
        config.W_MODEL_AGREE   * comp_model +
        config.W_ENS_TIGHTNESS * comp_tight +
        config.W_ENTRY_WINDOW  * comp_entry
    )
    confidence = round(min(max(confidence, 0.0), 1.0), 3)

    components = {
        "p_any_wins": round(comp_p_any, 3),
        "model_agreement": round(comp_model, 3),
        "ens_tightness": round(comp_tight, 3),
        "entry_window": round(comp_entry, 3),
    }

    # Build positions
    role_map = []
    if adj_low is not None:
        role_map.append((adj_low, Role.ADJ_LOW))
    role_map.append((primary, Role.PRIMARY))
    if adj_high is not None:
        role_map.append((adj_high, Role.ADJ_HIGH))

    positions: list[Position] = []
    for bucket, role in role_map:
        sl, tp = sl_tp_for(bucket.yes_price, role)
        ens_prob = (
            bundle.ensemble.prob_in_range(bucket.low, bucket.high)
            if bundle.ensemble else 0.0
        )
        positions.append(Position(
            bucket=bucket, role=role,
            entry_price=bucket.yes_price,
            sl=sl, tp=tp,
            ens_prob=round(ens_prob, 3),
        ))

    # Sizing
    size, label = config.position_size(confidence)

    # Entry gate
    enter = (
        confidence >= config.MIN_AGREEMENT_PROB and
        p_any >= config.MIN_AGREEMENT_PROB and
        any(config.ENTRY_PRICE_LOW <= p.entry_price <= config.ENTRY_PRICE_HIGH for p in positions)
    )

    skip_reason = None
    if not enter:
        reasons = []
        if confidence < config.MIN_AGREEMENT_PROB:
            reasons.append(f"confidence {confidence:.2f} < {config.MIN_AGREEMENT_PROB}")
        if p_any < config.MIN_AGREEMENT_PROB:
            reasons.append(f"P(any) {p_any:.2f} < {config.MIN_AGREEMENT_PROB}")
        if not any(config.ENTRY_PRICE_LOW <= p.entry_price <= config.ENTRY_PRICE_HIGH for p in positions):
            reasons.append("no bucket priced in 25-40¢ window")
        skip_reason = "; ".join(reasons) or "below entry threshold"

    return Recommendation(
        event=event, bundle=bundle, consensus=consensus,
        positions=positions,
        p_any_wins=round(p_any, 3),
        p_primary_wins=round(p_primary, 3),
        confidence=confidence,
        components=components,
        enter_signal=enter,
        skip_reason=skip_reason,
        sizing_label=label,
        sizing_per_position=size if enter else 0.0,
    )
