"""Telegram weather bot — main entrypoint with menu UI."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, JobQueue,
    CallbackQueryHandler, MessageHandler, filters,
)

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _send(update: Update, text: str, *, reply_markup=None):
    """Send HTML message, fall back to plain text on parse errors."""
    try:
        return await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup or messages.main_menu(),
        )
    except Exception as e:
        log.warning("HTML send failed (%s) — retrying as plain text", e)
        # crude tag strip
        plain = text
        for tag in ("<b>", "</b>", "<i>", "</i>", "<u>", "</u>",
                    "<s>", "</s>", "<code>", "</code>", "<pre>", "</pre>"):
            plain = plain.replace(tag, "")
        # remove <a href> tags but keep the URL inline
        import re
        plain = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2 (\1)", plain)
        return await update.effective_message.reply_text(
            plain, disable_web_page_preview=True,
            reply_markup=reply_markup or messages.main_menu(),
        )


async def _edit(query, text: str, *, reply_markup=None):
    try:
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception as e:
        log.warning("HTML edit failed: %s", e)


async def _build_card(client: httpx.AsyncClient, city, target):
    ev = await polymarket.fetch_event(client, city, target)
    if ev is None:
        return None, None, None
    bundle = await forecast.fetch_bundle(client, city, target)
    rec = strategy.build_recommendation(ev, bundle)
    htr = timeutil.hours_to_resolution(city, target)
    return rec, bundle, htr


# ---------------------------------------------------------------------------
# /start — show the persistent menu
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _send(update, messages.WELCOME)


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _send(update, messages.HELP)


async def cmd_cities(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _send(update, messages.cities_list_text())


# ---------------------------------------------------------------------------
# Scan family
# ---------------------------------------------------------------------------
async def _do_scan(update, days_range, only_enter, title):
    msg = await update.effective_message.reply_text("⏳ Scanning…")
    results = await scanner.scan_all(
        days_ahead_range=days_range, only_enterable=only_enter,
    )
    top = results[:12]
    text = messages.scan_summary(top, title=title)
    kb = messages.scan_keyboard(top)
    try:
        await msg.delete()
    except Exception:
        pass
    await _send(update, text, reply_markup=kb)


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    only_enter = bool(ctx.args) and "enter" in ctx.args
    title = "✓ Entry-gated picks" if only_enter else "🔥 Top opportunities (0-3d out)"
    await _do_scan(update, range(0, 4), only_enter, title)


async def cmd_today(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _do_scan(update, range(0, 1), False, "📅 Resolving today")


async def cmd_upcoming(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _do_scan(update, range(1, 4), False, "🔭 Upcoming (1-3d out)")


# ---------------------------------------------------------------------------
# /forecast
# ---------------------------------------------------------------------------
async def cmd_forecast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await _send(update, "<b>Pick a city:</b>", reply_markup=messages.city_picker("fc"))
    try:
        city = get_city(ctx.args[0])
    except KeyError as e:
        return await _send(update, f"❌ {_e(e)}")

    if len(ctx.args) >= 2:
        try:
            target = date.fromisoformat(ctx.args[1])
        except ValueError:
            return await _send(update, "❌ Date must be YYYY-MM-DD.")
    else:
        target = timeutil.target_date(city, 0)

    await _render_card(update, city, target)


async def _render_card(update, city, target):
    htr = timeutil.hours_to_resolution(city, target)
    if htr <= 0:
        return await _send(update, f"❌ Market for <b>{city.name}</b> {target} already resolved.")

    msg = await update.effective_message.reply_text("⏳ Loading…")
    async with httpx.AsyncClient() as client:
        rec, bundle, htr = await _build_card(client, city, target)
    try:
        await msg.delete()
    except Exception:
        pass

    if rec is None:
        slug = polymarket.build_slug(city, target)
        return await _send(
            update,
            f"❌ No live Polymarket event for <code>{slug}</code>.\n"
            f"<i>Polymarket may not have listed this date yet — try a different day.</i>",
        )

    is_tracked = tracker.get(city.slug, target) is not None
    text = messages.forecast_card(rec, htr)
    kb = messages.card_actions(city.slug, target.isoformat(), is_tracked)
    await _send(update, text, reply_markup=kb)


# ---------------------------------------------------------------------------
# /track /untrack /positions /refresh
# ---------------------------------------------------------------------------
async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await _send(update, "Usage: <code>/track [city] [YYYY-MM-DD]</code>")
    try:
        city = get_city(ctx.args[0])
        target = date.fromisoformat(ctx.args[1])
    except (KeyError, ValueError) as e:
        return await _send(update, f"❌ {_e(e)}")

    htr = timeutil.hours_to_resolution(city, target)
    if htr <= 0:
        return await _send(update, "❌ Market already resolved.")

    async with httpx.AsyncClient() as client:
        rec, _, _ = await _build_card(client, city, target)
    if rec is None:
        return await _send(update, f"❌ No event for slug <code>{polymarket.build_slug(city, target)}</code>.")

    tracker.add_or_update(rec, htr, action="INITIAL")
    await _send(update, f"✅ Tracking <b>{city.name}</b> {target} · conf {rec.confidence * 100:.0f}% · {rec.sizing_label}")
    await _render_card(update, city, target)


async def cmd_untrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2:
        return await _send(update, "Usage: <code>/untrack [city] [YYYY-MM-DD]</code>")
    try:
        city = get_city(ctx.args[0])
        target = date.fromisoformat(ctx.args[1])
    except (KeyError, ValueError) as e:
        return await _send(update, f"❌ {_e(e)}")
    if tracker.remove(city.slug, target):
        await _send(update, f"🗑 Stopped tracking <b>{city.name}</b> {target}.")
    else:
        await _send(update, "❌ Not tracked.")


async def cmd_positions(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    tracked = tracker.list_tracked()
    text = messages.tracker_summary(tracked)
    kb = messages.tracker_keyboard(tracked)
    await _send(update, text, reply_markup=kb)


async def cmd_refresh(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    tracked = tracker.list_tracked()
    if not tracked:
        return await _send(update, "<i>Nothing to refresh.</i>")
    msg = await update.effective_message.reply_text(f"⏳ Refreshing {len(tracked)} market(s)…")
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
                tracker.add_or_update(rec, htr, action="INITIAL")
            except Exception as exc:
                log.warning("Refresh failed for %s %s: %s", tm.city_slug, tm.target_date, exc)
    try:
        await msg.delete()
    except Exception:
        pass
    tracked = tracker.list_tracked()
    await _send(update, messages.tracker_summary(tracked), reply_markup=messages.tracker_keyboard(tracked))


# ---------------------------------------------------------------------------
# Reply-keyboard menu button taps  (text messages routed by content)
# ---------------------------------------------------------------------------
async def on_menu_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    if text.startswith("🔥"):
        return await cmd_scan(update, ctx)
    if text.startswith("✓"):
        ctx.args = ["enter"]
        return await cmd_scan(update, ctx)
    if text.startswith("📅"):
        return await cmd_today(update, ctx)
    if text.startswith("🔭"):
        return await cmd_upcoming(update, ctx)
    if text.startswith("📡"):
        return await cmd_positions(update, ctx)
    if text.startswith("🔄"):
        return await cmd_refresh(update, ctx)
    if text.startswith("🌍"):
        return await cmd_cities(update, ctx)
    if text.startswith("ℹ️") or text.lower().startswith("help"):
        return await cmd_help(update, ctx)
    # Anything else → show help
    return await cmd_help(update, ctx)


# ---------------------------------------------------------------------------
# Inline callback handler — city picker, date picker, track/untrack
# ---------------------------------------------------------------------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "noop":
        return

    parts = data.split(":")
    op = parts[0]

    if op == "fc":
        # fc:menu  → show city picker; fc:<slug> → show date picker
        sub = parts[1] if len(parts) > 1 else "menu"
        if sub == "menu":
            return await _edit(q, "<b>Pick a city:</b>", reply_markup=messages.city_picker("fc"))
        # show date picker for chosen city
        try:
            city = get_city(sub)
        except KeyError:
            return await _edit(q, "❌ Unknown city.")
        return await _edit(q, f"<b>{_e(city.name)}</b> — pick a date:",
                           reply_markup=messages.date_picker(city.slug, "fc"))

    if op == "fcd":
        # fcd:<slug>:<days_out>  → render card
        if len(parts) < 3:
            return
        try:
            city = get_city(parts[1])
            days_out = int(parts[2])
        except (KeyError, ValueError):
            return
        target = timeutil.target_date(city, days_out)
        await q.message.reply_text("⏳ Loading…")
        async with httpx.AsyncClient() as client:
            rec, _, htr = await _build_card(client, city, target)
        if rec is None:
            slug = polymarket.build_slug(city, target)
            return await q.message.reply_text(
                f"❌ No live Polymarket event for {slug}.",
            )
        is_tracked = tracker.get(city.slug, target) is not None
        kb = messages.card_actions(city.slug, target.isoformat(), is_tracked)
        try:
            await q.message.reply_text(
                messages.forecast_card(rec, htr),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=kb,
            )
        except Exception as e:
            log.warning("Card render failed: %s", e)
        return

    if op == "track":
        if len(parts) < 3:
            return
        try:
            city = get_city(parts[1])
            target = date.fromisoformat(parts[2])
        except (KeyError, ValueError):
            return
        htr = timeutil.hours_to_resolution(city, target)
        if htr <= 0:
            return await q.message.reply_text("❌ Market already resolved.")
        async with httpx.AsyncClient() as client:
            ev = await polymarket.fetch_event(client, city, target)
            if ev is None:
                return await q.message.reply_text("❌ Event not found.")
            bundle = await forecast.fetch_bundle(client, city, target)
        rec = strategy.build_recommendation(ev, bundle)
        tracker.add_or_update(rec, htr, action="INITIAL")
        return await q.message.reply_text(
            f"✅ Tracking <b>{_e(city.name)}</b> {target} · conf {rec.confidence * 100:.0f}%",
            parse_mode=ParseMode.HTML,
        )

    if op == "untrack":
        if len(parts) < 3:
            return
        ok = tracker.remove(parts[1], parts[2])
        return await q.message.reply_text("🗑 Removed." if ok else "❌ Not tracked.")

    if op == "view":
        # view tracked card
        if len(parts) < 3:
            return
        try:
            city = get_city(parts[1])
            target = date.fromisoformat(parts[2])
        except (KeyError, ValueError):
            return
        async with httpx.AsyncClient() as client:
            rec, _, htr = await _build_card(client, city, target)
        if rec is None:
            return await q.message.reply_text("❌ Event not found.")
        is_tracked = tracker.get(city.slug, target) is not None
        kb = messages.card_actions(city.slug, target.isoformat(), is_tracked)
        await q.message.reply_text(
            messages.forecast_card(rec, htr),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=kb,
        )
        return

    if op == "refresh1":
        if len(parts) < 3:
            return
        try:
            city = get_city(parts[1])
            target = date.fromisoformat(parts[2])
        except (KeyError, ValueError):
            return
        async with httpx.AsyncClient() as client:
            rec, _, htr = await _build_card(client, city, target)
        if rec is None:
            return await q.message.reply_text("❌ Event not found.")
        is_tracked = tracker.get(city.slug, target) is not None
        kb = messages.card_actions(city.slug, target.isoformat(), is_tracked)
        await q.message.reply_text(
            messages.forecast_card(rec, htr),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=kb,
        )
        return


def _e(s) -> str:
    import html as _h
    return _h.escape(str(s), quote=False)


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------
async def scheduled_scan(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = config.TELEGRAM_CHAT_ID
    if not chat_id:
        return
    results = await scanner.scan_all(days_ahead_range=range(0, 4), only_enterable=True)
    if not results:
        return
    msg = messages.scan_summary(results[:10], title="🔔 Scheduled scan — entry-gated picks")
    await ctx.bot.send_message(
        chat_id=chat_id, text=msg,
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        reply_markup=messages.scan_keyboard(results[:10]),
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
                    alerts.append(f"⏹ <b>{city.name}</b> {target} resolved")
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
                        f"⚠️ <b>{city.name}</b> {target}: <b>{action}</b> "
                        f"({delta:+.0f}% conf, now {rec.sizing_label})"
                    )
            except Exception as exc:
                log.warning("Auto-refresh failed for %s: %s", tm.city_slug, exc)
    if alerts:
        await ctx.bot.send_message(
            chat_id=chat_id, text="<b>📡 Tracker updates</b>\n\n" + "\n".join(alerts),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN env var is required")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler(["start"], cmd_start))
    app.add_handler(CommandHandler(["help"], cmd_help))
    app.add_handler(CommandHandler("cities", cmd_cities))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CommandHandler("forecast", cmd_forecast))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("refresh", cmd_refresh))

    # Inline callback (city/date pickers, track/view)
    app.add_handler(CallbackQueryHandler(on_callback))

    # Reply-keyboard taps come in as plain text → route by content
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_menu_text))

    jq: JobQueue = app.job_queue
    if jq is not None:
        for hour in config.SCAN_HOURS_UTC:
            jq.run_daily(
                scheduled_scan,
                time=datetime.now(tz=timezone.utc).time().replace(
                    hour=hour, minute=0, second=0, microsecond=0,
                ),
            )
        jq.run_repeating(scheduled_refresh, interval=3600, first=60)

    log.info("Bot starting — %d cities loaded", len(ALL_CITIES))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
