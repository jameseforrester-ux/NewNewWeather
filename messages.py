"""Telegram message formatters and keyboard menus.

Uses HTML mode (not Markdown) — way more reliable than legacy markdown,
no escaping headaches with em-dashes, parentheses, accented city names, etc.
Telegram HTML supports: <b> <i> <u> <s> <code> <pre> <a href> and that's it.
"""

from __future__ import annotations

import html

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)

from cities import US_CITIES, INTL_CITIES
from scanner import ScanResult
from strategy import Recommendation, Role
from tracker import TrackedMarket
import timeutil


# ---------------------------------------------------------------------------
# Persistent main menu (shows under the text input)
# ---------------------------------------------------------------------------
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔥 Top picks"), KeyboardButton("✓ Entry signals")],
            [KeyboardButton("📅 Today"), KeyboardButton("🔭 Upcoming")],
            [KeyboardButton("📡 Positions"), KeyboardButton("🔄 Refresh")],
            [KeyboardButton("🌍 Cities"), KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ---------------------------------------------------------------------------
# Inline city pickers
# ---------------------------------------------------------------------------
def city_picker(action: str = "fc") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton("── US ──", callback_data="noop")])
    row: list[InlineKeyboardButton] = []
    for c in US_CITIES:
        row.append(InlineKeyboardButton(c.name, callback_data=f"{action}:{c.slug}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)

    rows.append([InlineKeyboardButton("── International ──", callback_data="noop")])
    row = []
    for c in INTL_CITIES:
        row.append(InlineKeyboardButton(c.name, callback_data=f"{action}:{c.slug}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)


def date_picker(city_slug: str, action: str = "fc") -> InlineKeyboardMarkup:
    labels = ["Today", "+1 day", "+2 days", "+3 days", "+4 days"]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, label in enumerate(labels):
        row.append(InlineKeyboardButton(label, callback_data=f"{action}d:{city_slug}:{i}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("« Back to cities", callback_data=f"{action}:menu")])
    return InlineKeyboardMarkup(rows)


def card_actions(city_slug: str, target_iso: str, is_tracked: bool) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(
            "🗑 Stop tracking" if is_tracked else "📡 Track this market",
            callback_data=f"{'untrack' if is_tracked else 'track'}:{city_slug}:{target_iso}",
        ),
        InlineKeyboardButton("🔄 Re-score", callback_data=f"refresh1:{city_slug}:{target_iso}"),
    ]]
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Card components
# ---------------------------------------------------------------------------
def _e(s) -> str:
    return html.escape(str(s), quote=False)


def _temp(v: float | None, unit: str) -> str:
    return f"{v:.1f}°{unit}" if v is not None else "—"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def _bar(p: float, width: int = 12) -> str:
    filled = max(0, min(width, round(p * width)))
    return "█" * filled + "░" * (width - filled)


_ROLE_BADGE = {
    Role.ADJ_LOW:  "ADJ−",
    Role.PRIMARY:  "★ PRIMARY",
    Role.ADJ_HIGH: "ADJ+",
}

_TIER_EMOJI = {
    "MAX":    "🟣",
    "STRONG": "🔵",
    "BASE":   "🟢",
    "PROBE":  "🟡",
    "SKIP":   "⚪",
}


# ---------------------------------------------------------------------------
# Forecast card
# ---------------------------------------------------------------------------
    # Live METAR
    live_line = ""
    if rec.bundle.metar and rec.bundle.metar.temp_f is not None:
        obs = rec.bundle.metar
        time_str = obs.obs_time.strftime("%H:%MZ") if obs.obs_time else "recent"
        live_line = f"📡 <b>Live station</b>: {obs.temp_str} at {time_str}\n"
        
    def forecast_card(rec: Recommendation, htr: float) -> str:
    city = rec.event.city
    rel = timeutil.format_relative(city, rec.event.target_date)
    when = rec.event.target_date.strftime("%a %b %d")

    header = (
        f"<b>{_e(city.name)}</b>  ·  <code>{_e(city.station_code)}</code>\n"
        f"<i>{_e(when)} · {_e(rel)}</i>\n"
        f"<i>resolves at {city.unit}° on {_e(city.source_label)}</i>"
    )
    if city.ai_trap:
        header += f"\n⚠️ <i>AI summaries may cite {_e(city.ai_trap)} — actual: {_e(city.station_code)}</i>"

    consensus = _temp(rec.consensus, city.unit)
    tier_emoji = _TIER_EMOJI.get(rec.sizing_label, "⚪")
    if rec.enter_signal:
        tier_line = f"{tier_emoji} <b>{_e(rec.sizing_label)}</b> · ${rec.sizing_per_position:.2f}/bucket"
    else:
        tier_line = f"{tier_emoji} <b>{_e(rec.sizing_label)}</b>"

    metrics = (
        f"\n📊 Consensus <b>{_e(consensus)}</b>   "
        f"P(any) <b>{_pct(rec.p_any_wins)}</b>\n"
        f"🎯 Confidence <b>{_pct(rec.confidence)}</b>  <code>{_bar(rec.confidence)}</code>\n"
        f"💼 {tier_line}"
    )

    bucket_lines = ["<pre>"]
    bucket_lines.append("  Role        Bucket          Price   ENS")
    bucket_lines.append("  ──────────  ──────────────  ──────  ────")
    for p in rec.positions:
        badge = _ROLE_BADGE[p.role]
        ens = _pct(p.ens_prob)
        price = f"{p.entry_price * 100:>4.0f}¢"
        line = f"  {badge:<10}  {p.bucket.label:<14}  {price:>6}  {ens:>4}"
        bucket_lines.append(line)
    bucket_lines.append("</pre>")
    buckets_block = "\n".join(bucket_lines)

    model_lines = ["<pre>"]
    model_lines.append("  Model           Forecast")
    model_lines.append("  ──────────────  ─────────────────")
    for m in rec.bundle.deterministic:
        flag = ""
        if city.slug == "denver" and m.model_id == "gfs_global":
            flag = "  (down-weighted)"
        model_lines.append(f"  {m.name:<14}  {_temp(m.daily_max, city.unit):<8}{flag}")
    if rec.bundle.hrrr is not None:
        if rec.bundle.hrrr.daily_max is not None:
            model_lines.append(f"  {'HRRR':<14}  {_temp(rec.bundle.hrrr.daily_max, city.unit):<8}  (0-48h)")
        else:
            model_lines.append(f"  {'HRRR':<14}  —         (out of range)")
    if rec.bundle.ensemble:
        ens = rec.bundle.ensemble
        med = _temp(ens.median, city.unit)
        sd = f"±{ens.stdev:.1f}" if ens.stdev is not None else "—"
        model_lines.append(f"  {ens.name:<14}  {med}  {sd}  ({len(ens.members)}m)")
    model_lines.append("</pre>")
    models_block = "\n".join(model_lines)

    if rec.enter_signal:
        plan_lines = ["<b>📋 Entry plan</b>"]
        for p in rec.positions:
            if p.role is Role.PRIMARY:
                plan_lines.append(
                    f"  ★ Hold <code>{_e(p.bucket.label)}</code> to resolution · SL {p.sl * 100:.0f}¢"
                )
            else:
                tp = f"TP {p.tp * 100:.0f}¢" if p.tp else "—"
                plan_lines.append(
                    f"  · <code>{_e(p.bucket.label)}</code> SL {p.sl * 100:.0f}¢ / {tp}"
                )
        plan_lines.append(f"\n⏱ <b>{htr:.0f}h</b> to resolution")
        plan = "\n".join(plan_lines)
    else:
        plan = f"❌ <b>Skip:</b> <i>{_e(rec.skip_reason)}</i>"

    links = (
        f"\n🔗 <a href=\"{city.source_url}\">{_e(city.source_label)}</a> · "
        f"<a href=\"https://polymarket.com/event/{rec.event.slug}\">Polymarket</a>"
    )

    return "\n\n".join([header, metrics, buckets_block, models_block, plan + links])


# ---------------------------------------------------------------------------
# Scan summary
# ---------------------------------------------------------------------------
def scan_summary(results: list[ScanResult], title: str) -> str:
    if not results:
        return (
            f"<b>{_e(title)}</b>\n\n"
            "<i>No opportunities found in the current scan window.</i>\n"
            "<i>Polymarket may not have listed forward markets yet — try again later.</i>"
        )

    lines = [f"<b>{_e(title)}</b>", ""]
    lines.append("<pre>")
    lines.append("  Conf  P-any  Tier     City · when")
    lines.append("  ────  ─────  ───────  ──────────────────────────")
    for r in results:
        rec = r.rec
        city = rec.event.city
        rel = timeutil.format_relative(city, rec.event.target_date)
        flag = "✓" if rec.enter_signal else " "
        lines.append(
            f"{flag} {rec.confidence * 100:>3.0f}%  {rec.p_any_wins * 100:>3.0f}%   "
            f"{rec.sizing_label:<7}  {city.name} · {rel}"
        )
    lines.append("</pre>")
    lines.append("")
    lines.append("<i>Tap a city below for the full card.</i>")
    return "\n".join(lines)


def scan_keyboard(results: list[ScanResult]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    seen: set[str] = set()
    for r in results[:18]:
        city = r.rec.event.city
        days_out = max(0, (r.rec.event.target_date - timeutil.station_today(city)).days)
        key = f"{city.slug}:{days_out}"
        if key in seen:
            continue
        seen.add(key)
        emoji = _TIER_EMOJI.get(r.rec.sizing_label, "⚪")
        label = f"{emoji} {city.name} +{days_out}d"
        row.append(InlineKeyboardButton(label, callback_data=f"fcd:{city.slug}:{days_out}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None


# ---------------------------------------------------------------------------
# Tracker view
# ---------------------------------------------------------------------------
def tracker_summary(tracked: list[TrackedMarket]) -> str:
    if not tracked:
        return (
            "<b>📡 Tracked positions</b>\n\n"
            "<i>You're not tracking any markets yet.</i>\n"
            "Find one with 🔥 Top picks, then tap 📡 Track this market on its card."
        )

    lines = ["<b>📡 Tracked positions</b>", ""]
    lines.append("<pre>")
    lines.append("  Conf  Δ      Action  City · target")
    lines.append("  ────  ─────  ──────  ──────────────────")
    for tm in tracked:
        cp = tm.latest
        if cp is None:
            continue
        delta = (cp.confidence - tm.initial_confidence) * 100
        arrow = "↑" if delta > 5 else "↓" if delta < -5 else "→"
        lines.append(
            f"  {cp.confidence * 100:>3.0f}%  {arrow}{abs(delta):>4.0f}%  "
            f"{cp.action:<6}  {tm.city_slug} · {tm.target_date}"
        )
    lines.append("</pre>")
    lines.append("")
    lines.append("<i>Hourly auto-refresh; tap 🔄 Refresh for now.</i>")
    return "\n".join(lines)


def tracker_keyboard(tracked: list[TrackedMarket]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for tm in tracked[:12]:
        label = f"{tm.city_slug} {tm.target_date[5:]}"
        row.append(InlineKeyboardButton(label, callback_data=f"view:{tm.city_slug}:{tm.target_date}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows) if rows else None


# ---------------------------------------------------------------------------
# Help & cities
# ---------------------------------------------------------------------------
HELP = (
    "<b>🌡 Polymarket weather bot</b>\n\n"
    "Use the menu below for everyday actions, or type:\n\n"
    "<b>/scan</b> — top picks across all 36 cities\n"
    "<b>/today</b> · <b>/upcoming</b> — by horizon\n"
    "<b>/forecast</b> [city] [date] — full card\n"
    "<b>/track</b> [city] [date] — start tracking\n"
    "<b>/positions</b> · <b>/refresh</b> · <b>/cities</b>\n\n"
    "<b>Sizing tiers</b>\n"
    "🟣 MAX 2.0× · 🔵 STRONG 1.5× · 🟢 BASE 1.0× · 🟡 PROBE 0.5× · ⚪ SKIP\n\n"
    "<b>Strategy</b>\n"
    "Three-bucket structure on ECMWF/AIFS/GraphCast consensus.\n"
    "Phase 1: enter all 3 at 25-40¢, 3 days out.\n"
    "Phase 2 (12-24h): SL on collapsing low, TP on adj reprice, primary held to resolution."
)


def cities_list_text() -> str:
    lines = ["<b>🌍 Tracked cities</b>", ""]
    lines.append("<b>US (°F, 2° buckets)</b>")
    for c in US_CITIES:
        trap = f"  ⚠️ <i>AI cites {_e(c.ai_trap)}</i>" if c.ai_trap else ""
        lines.append(f"  • <b>{_e(c.name)}</b> — <code>{_e(c.station_code)}</code>{trap}")
    lines.append("")
    lines.append("<b>International (°C, 1° buckets)</b>")
    for c in INTL_CITIES:
        src = "" if c.resolution_source == "wunderground" else f" <i>({_e(c.source_label)})</i>"
        lines.append(f"  • <b>{_e(c.name)}</b> — <code>{_e(c.station_code)}</code>{src}")
    return "\n".join(lines)


WELCOME = (
    "<b>👋 Welcome to your Polymarket weather bot</b>\n\n"
    "Tracking <b>36 cities</b> across Wunderground + HK Observatory.\n"
    "Tap <b>🔥 Top picks</b> below to see what's worth a look right now."
)
