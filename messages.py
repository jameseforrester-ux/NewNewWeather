"""Telegram message formatters.

All output uses Markdown (legacy, not V2 — simpler escaping rules and supports
the formatting density we need without per-character escaping).

Visual structure mirrors the in-chat card preview:
  - Header: City · Station · local-relative date
  - Metrics: consensus / P(any wins) / confidence / sizing tier
  - Buckets: ADJ_LOW · PRIMARY · ADJ_HIGH with prices and ENS probs
  - Models: ECMWF / AIFS / GraphCast / GFS / HRRR readings
  - Footer: SL/TP plan or skip reason
"""

from __future__ import annotations

import textwrap

from cities import City
from scanner import ScanResult
from strategy import Position, Recommendation, Role
from tracker import TrackedMarket
import timeutil


_ROLE_BADGE = {Role.ADJ_LOW: "ADJ-", Role.PRIMARY: "★ PRIMARY", Role.ADJ_HIGH: "ADJ+"}


def _fmt_temp(v: float | None, unit: str) -> str:
    return f"{v:.1f}°{unit}" if v is not None else "—"


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def _bar(p: float, width: int = 10) -> str:
    filled = round(p * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Forecast card — single market detail
# ---------------------------------------------------------------------------
def forecast_card(rec: Recommendation, htr: float) -> str:
    city = rec.event.city
    rel = timeutil.format_relative(city, rec.event.target_date)
    when = rec.event.target_date.strftime("%a %b %-d") if hasattr(rec.event.target_date, "strftime") else str(rec.event.target_date)

    lines = []
    lines.append(f"*{city.name}* · `{city.station_code}` · {when} ({rel})")
    lines.append(f"_{city.station_name}_ — resolves at {city.unit}° on {city.source_label}")
    if city.ai_trap:
        lines.append(f"⚠️ AI summaries may cite {city.ai_trap} — resolution is `{city.station_code}`")
    lines.append("")

    # Top metrics
    consensus = _fmt_temp(rec.consensus, city.unit)
    sizing = f"{rec.sizing_label}  ${rec.sizing_per_position:.2f}/bucket" if rec.enter_signal else rec.sizing_label
    lines.append(f"📊 *Consensus*: {consensus}    *P(any wins)*: {_fmt_pct(rec.p_any_wins)}")
    lines.append(f"🎯 *Confidence*: {_fmt_pct(rec.confidence)} {_bar(rec.confidence)}")
    lines.append(f"💼 *Sizing*: {sizing}")
    lines.append("")

    # Buckets
    lines.append("*Buckets*")
    for p in rec.positions:
        badge = _ROLE_BADGE[p.role]
        ens = _fmt_pct(p.ens_prob)
        line = f"  {badge:10}  `{p.bucket.label:14}`  {p.entry_price * 100:.0f}¢  ENS {ens}"
        lines.append(line)
    lines.append("")

    # Model breakdown
    lines.append("*Models*")
    for m in rec.bundle.deterministic:
        flag = ""
        if city.slug == "denver" and m.model_id == "gfs_global":
            flag = " _(down-weighted: cold-air damming)_"
        lines.append(f"  • {m.name:14} {_fmt_temp(m.daily_max, city.unit)}{flag}")
    if rec.bundle.hrrr is not None:
        if rec.bundle.hrrr.daily_max is not None:
            lines.append(f"  • HRRR           {_fmt_temp(rec.bundle.hrrr.daily_max, city.unit)}  _(0-48h)_")
        else:
            lines.append("  • HRRR           — _(out of range, >48h)_")
    if rec.bundle.ensemble:
        ens = rec.bundle.ensemble
        med = _fmt_temp(ens.median, city.unit)
        sd = f"±{ens.stdev:.1f}" if ens.stdev is not None else ""
        lines.append(f"  • {ens.name}  {med} {sd} ({len(ens.members)} members)")
    lines.append("")

    # SL/TP plan or skip reason
    if rec.enter_signal:
        lines.append("*Entry plan*")
        for p in rec.positions:
            if p.role is Role.PRIMARY:
                lines.append(f"  ★ Hold `{p.bucket.label}` to resolution · SL {p.sl * 100:.0f}¢")
            else:
                tp = f"TP {p.tp * 100:.0f}¢" if p.tp else "—"
                lines.append(f"  · `{p.bucket.label}` SL {p.sl * 100:.0f}¢ / {tp}")
        lines.append("")
        lines.append(f"⏱ {htr:.1f}h to resolution · /track {city.slug} {rec.event.target_date}")
    else:
        lines.append(f"❌ *Skip*: {rec.skip_reason}")

    lines.append("")
    lines.append(f"🔗 [{city.source_label}]({city.source_url}) · [Polymarket](https://polymarket.com/event/{rec.event.slug})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scan summary — top-N across all cities
# ---------------------------------------------------------------------------
def scan_summary(results: list[ScanResult], title: str = "🔥 Top opportunities") -> str:
    if not results:
        return "_No opportunities found in the current scan window._"

    lines = [f"*{title}*", ""]
    lines.append("`Conf  P-any  $/buc  Tier      City · date · station`")
    for r in results:
        rec = r.rec
        city = rec.event.city
        rel = timeutil.format_relative(city, rec.event.target_date)
        size = f"${rec.sizing_per_position:.2f}" if rec.enter_signal else " —"
        flag = "✓" if rec.enter_signal else "·"
        lines.append(
            f"`{flag} {rec.confidence * 100:3.0f}%  {rec.p_any_wins * 100:3.0f}%  {size:>5}  {rec.sizing_label:8}` "
            f"{city.name} · {rel} · `{city.station_code}`"
        )
    lines.append("")
    lines.append("_Tap any city with `/forecast <city>` for the full card._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tracker view — currently watched markets with refinement deltas
# ---------------------------------------------------------------------------
def tracker_summary(tracked: list[TrackedMarket]) -> str:
    if not tracked:
        return "_You're not tracking any markets yet. Use `/track <city> <YYYY-MM-DD>`._"

    lines = ["*📡 Tracked positions*", ""]
    for tm in tracked:
        cp = tm.latest
        if cp is None:
            continue
        delta = cp.confidence - tm.initial_confidence
        arrow = "↑" if delta > 0.05 else "↓" if delta < -0.05 else "→"
        lines.append(
            f"• `{tm.city_slug:12}` {tm.target_date}  "
            f"conf {cp.confidence * 100:.0f}% {arrow} ({delta * 100:+.0f}%)  "
            f"{cp.action} · {cp.sizing_label}"
        )
        lines.append(
            f"   {cp.hours_to_resolution:.0f}h out · primary {cp.p_primary_wins * 100:.0f}% · "
            f"any {cp.p_any_wins * 100:.0f}%"
        )
    lines.append("")
    lines.append("_Refresh anytime with `/refresh`. Remove with `/untrack <city> <date>`._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Help / cities
# ---------------------------------------------------------------------------
HELP = textwrap.dedent("""\
    *Polymarket weather bot*

    *Daily flow*
    `/scan` — confidence-ranked picks across all 35 cities, 0–3 days out
    `/today` — markets resolving today (per-city local time)
    `/upcoming` — markets resolving in the next 1–3 days
    `/forecast <city> [YYYY-MM-DD]` — full card for one city/date
    `/track <city> <YYYY-MM-DD>` — start tracking a market through resolution
    `/positions` — your tracked markets with confidence drift
    `/untrack <city> <YYYY-MM-DD>` — stop tracking
    `/refresh` — re-score all tracked markets right now
    `/cities` — list all 35 cities and their resolution stations
    `/help` — this message

    *Confidence tiers (sizing)*
    ≥90% MAX 2.0× · ≥80% STRONG 1.5× · ≥70% BASE 1.0× · ≥60% PROBE 0.5× · <60% SKIP

    *Strategy primer*
    Three-bucket structure centered on ECMWF/AIFS/GraphCast consensus.
    Phase 1 entry 25–40¢. Phase 2 fires SL on collapsing low + adj TP on reprice.
    Primary held to resolution.
    """).strip()


def cities_list(cities: list[City]) -> str:
    lines = ["*Tracked cities*", ""]
    by_unit = {"F": [], "C": []}
    for c in cities:
        trap = f"  ⚠️ AI cites {c.ai_trap}" if c.ai_trap else ""
        by_unit[c.unit].append(f"  • `{c.slug:14}` {c.name} — `{c.station_code}` ({c.station_name}){trap}")
    if by_unit["F"]:
        lines.append("*US (°F, 2° buckets)*")
        lines.extend(by_unit["F"])
        lines.append("")
    if by_unit["C"]:
        lines.append("*International (°C, 1° buckets)*")
        lines.extend(by_unit["C"])
    return "\n".join(lines)
