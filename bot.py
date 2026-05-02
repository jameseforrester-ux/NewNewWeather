"""Telegram bot — main entrypoint.

Commands
--------
  /scan                          — top 10 across all cities, 0-3d out
  /scan all                      — full ranked scan (no truncation)
  /scan enter                    — only markets passing entry gate
  /today                         — markets resolving today (per local TZ)
  /upcoming                      — 1-3 days out
  /forecast <city> [YYYY-MM-DD]  — full card
  /track <city> <YYYY-MM-DD>     — start tracking
  /untrack <city> <YYYY-MM-DD>   — stop tracking
  /positions                     — currently tracked with refinement deltas
  /refresh                       — re-score all tracked markets now
  /cities                        — list all 35 cities
  /help                          — command list

Background jobs
---------------
  Scheduled scans: 04:00 + 16:00 UTC (after major model runs ingest)
  Tracked refresh: every hour for markets <48h out, every 6h otherwise
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import httpx
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, JobQueue,
)
from telegram.constants import ParseMode

import config
import forecast
import messages
import polymarket
import scanner
import strategy
import timeutil
import tracker
from cities import ALL_CITIES, get_city


log = logging.getLogger("weatherbot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _send(update: Update, text: str):
    return update.effective_message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _send(update, messages.HELP)


async def cmd_cities(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _send(update, messages.cities_list(ALL_CITIES))


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args or []
    only_enter = "enter" in args
    show_all = "all" in args
    await update.effective_message.reply_text("⏳ Scanning all cities, 0-3d out…")

    results = await scanner.scan_all(
        days_ahead_range=range(0, 4),
        only_enterable=only_enter,
    )
    if not show_all:
        results = results[:10]

    title = "✓ Entry-gated picks" if only_enter else "🔥 Top opportunities (0-3d out)"
    await _send(update, messages.scan_summary(results, title=title))


async def cmd_today(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("⏳ Fetching markets live today…")
    results = await scanner.scan_all(days_ahead_range=range(0, 1))
    await _send(update, messages.scan_summary(results, title="📅 Resolving today"))


async def cmd_upcoming(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("⏳ Scanning 1-3 days out…")
    results = await scanner.scan_all(days_ahead_range=range(1, 4))
    await _send(update, messages.scan_summary(results, title="🔭 Upcoming (1-3d out)"))


async def cmd_forecast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await _send(update, "Usage: `/forecast <city> [YYYY-MM-DD]`")
    try:
        city = get_city(ctx.args[0])
    except KeyError as e:
        return await _send(update, f"❌ {e}")

    if len(ctx.args) >= 2:
        try:
            target = date.fromisoformat(ctx.args[1])
        except ValueError:
            return await _send(update, "❌ Date must be YYYY-MM-DD.")
    else:
        target = timeutil.target_date(city, 0)

    htr = timeutil.hours_to_resolution(city, target)
    if htr <= 0:
        return await _send(update, f"❌ Market for {city.name} {target} already resolved ({htr:.0f}h ago, station local).")

    async with httpx.AsyncClient() as client:
        ev = await polymarket.fetch_event(client, city, target)
        if ev is None:
            return await _send(update, f"❌ No Polymarket event found for slug `{polymarket.build_slug(city, target)}`.")
        bundle = await forecast.fetch_bundle(client, city, target)

    rec = strategy.build_recommendation(ev, bundle)
    await _send(update, messages.forecast_card(rec, htr))


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await _send(update, "Usage: `/track <city> <YYYY-MM-DD>`")
    try:
        city = get_city(ctx.args[0])
    except KeyError as e:
        return await _send(update, f"❌ {e}")
    try:
        target = date.fromisoformat(ctx.args[1])
    except ValueError:
        return await _send(update, "❌ Date must be YYYY-MM-DD.")

    htr = timeutil.hours_to_resolution(city, target)
    if htr <= 0:
        return await _send(update, f"❌ Market already resolved.")

    async with httpx.AsyncClient() as client:
        ev = await polymarket.fetch_event(client, city, target)
        if ev is None:
            return await _send(update, f"❌ No event for slug `{polymarket.build_slug(city, target)}`.")
        bundle = await forecast.fetch_bundle(client, city, target)
    rec = strategy.build_recommendation(ev, bundle)

    tm = tracker.add_or_update(rec, htr, action="INITIAL")
    await _send(update, f"✅ Tracking {city.name} {target} · conf {rec.confidence * 100:.0f}% · {rec.sizing_label}")
    await _send(update, messages.forecast_card(rec, htr))


async def cmd_untrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await _send(update, "Usage: `/untrack <city> <YYYY-MM-DD>`")
    try:
        city = get_city(ctx.args[0])
        target = date.fromisoformat(ctx.args[1])
    except (KeyError, ValueError) as e:
        return await _send(update, f"❌ {e}")
    if tracker.remove(city.slug, target):
        await _send(update, f"🗑 Stopped tracking {city.name} {target}.")
    else:
        await _send(update, "❌ Not tracked.")


async def cmd_positions(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    tracked = tracker.list_tracked()
    await _send(update, messages.tracker_summary(tracked))


async def cmd_refresh(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    tracked = tracker.list_tracked()
    if not tracked:
        return await _send(update, "_Nothing to refresh._")
    await update.effective_message.reply_text(f"⏳ Refreshing {len(tracked)} tracked market(s)…")
    async with httpx.AsyncClient() as client:
        for tm in tracked:
            try:
                city = get_city(tm.city_slug)
                target = date.fromisoformat(tm.target_date)
                htr = timeutil.hours_to_resolution(city, target)
                if htr <= 0:
                    tracker.close(city.slug, target, "resolved")
                    continue
                ev = await polymarket.fetch_event(client, city, target)
                if ev is None:
                    continue
                bundle = await forecast.fetch_bundle(client, city, target)
                rec = strategy.build_recommendation(ev, bundle)
                tracker.add_or_update(rec, htr, action="INITIAL")  # action filled by tracker
            except Exception as exc:  # noqa: BLE001
                log.warning("Refresh failed for %s %s: %s", tm.city_slug, tm.target_date, exc)
    await _send(update, messages.tracker_summary(tracker.list_tracked()))


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------
async def scheduled_scan(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        log.warning("TELEGRAM_CHAT_ID not set; skipping scheduled scan")
        return
    results = await scanner.scan_all(days_ahead_range=range(0, 4), only_enterable=True)
    if not results:
        return
    msg = messages.scan_summary(results[:10], title="🔔 Scheduled scan — entry-gated picks")
    await ctx.bot.send_message(
        chat_id=chat_id, text=msg,
        parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
    )


async def scheduled_refresh(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        return
    tracked = tracker.list_tracked()
    if not tracked:
        return
    alerts: list[str] = []
    async with httpx.AsyncClient() as client:
        for tm in tracked:
            try:
                city = get_city(tm.city_slug)
                target = date.fromisoformat(tm.target_date)
                htr = timeutil.hours_to_resolution(city, target)
                if htr <= 0:
                    tracker.close(city.slug, target, "resolved")
                    alerts.append(f"⏹ {city.name} {target} resolved")
                    continue
                ev = await polymarket.fetch_event(client, city, target)
                if ev is None:
                    continue
                bundle = await forecast.fetch_bundle(client, city, target)
                rec = strategy.build_recommendation(ev, bundle)
                prev_conf = tm.latest.confidence if tm.latest else tm.initial_confidence
                action = config.refinement_action(prev_conf, rec.confidence)
                tracker.add_or_update(rec, htr, action=action)
                if action in {"ADD", "TRIM", "EXIT"}:
                    delta = (rec.confidence - prev_conf) * 100
                    alerts.append(
                        f"⚠️ {city.name} {target}: {action} ({delta:+.0f}% conf, {rec.sizing_label})"
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("Auto-refresh failed for %s: %s", tm.city_slug, exc)
    if alerts:
        await ctx.bot.send_message(
            chat_id=chat_id, text="*Tracked-market updates*\n\n" + "\n".join(alerts),
            parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
        )


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN env var is required")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("cities", cmd_cities))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CommandHandler("forecast", cmd_forecast))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("refresh", cmd_refresh))

    jq: JobQueue = app.job_queue
    # Scheduled scans after major model runs ingest
    for hour in config.SCAN_HOURS_UTC:
        jq.run_daily(scheduled_scan, time=datetime.now(tz=timezone.utc).time().replace(hour=hour, minute=0, second=0, microsecond=0))
    # Hourly tracked-market refresh
    jq.run_repeating(scheduled_refresh, interval=3600, first=60)

    log.info("Bot starting — %d cities loaded", len(ALL_CITIES))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
