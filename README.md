# Polymarket weather bot

Telegram bot that scans every Polymarket "Highest temperature in &lt;city&gt;" market across 35 cities, ranks them by confidence using ECMWF / AIFS / GraphCast / GFS / HRRR, and walks you through the Two-Phase three-bucket strategy from 3 days out through resolution.

## What it does

1. **Scans 35 cities** (9 US, 26 international) on a recurring schedule and surfaces the highest-confidence opportunities.
2. **Computes a 3-bucket entry plan** centered on multi-model consensus: an `ADJ_LOW`, `PRIMARY`, and `ADJ_HIGH` bucket, sized by confidence tier.
3. **Tracks positions through resolution.** Refreshes hourly. Tells you to ADD when confidence rises, TRIM when it falls, EXIT below threshold.
4. **Respects each city's local timezone.** Tokyo's "May 2" market is queried using Tokyo's local date, not yours — so you never see a market that's already resolved on the other side of the dateline.
5. **Flags AI-summary traps.** Polymarket's AI summaries on Denver and NYC events sometimes cite the wrong station (KDEN, KNYC) — the bot calls these out so you don't anchor on the wrong reading.

## Resolution sources verified

Most markets resolve to:
- Source: Wunderground, "all times on this day"
- Window: full calendar day in the station's local timezone
- Precision: whole degree (°F US, °C international)
- Bucket width: 2°F US, 1°C international

**One exception flagged:** Hong Kong does NOT use Wunderground. HK markets resolve to the Hong Kong Observatory's "Absolute Daily Max (deg. C)" from the Daily Extract at `weather.gov.hk/en/cis/climat.htm`, with **0.1°C precision** (not whole degrees). The bot handles this automatically — `cities.py` carries a `resolution_source` field.

US: NYC `KLGA`, Chicago `KORD`, LA `KLAX`, Miami `KMIA`, Denver `KBKF`, Atlanta `KATL`, Seattle `KSEA`, Houston `KHOU`, Austin `KAUS`.
Intl: Seoul `RKSI`, Busan `RKPK`, Tokyo `RJTT`, Hong Kong `HKO` (HK Observatory direct), Singapore `WSSS`, Shanghai `ZSPD`, Beijing `ZBAA`, Shenzhen `ZGSZ`, Guangzhou `ZGGG`, Wuhan `ZHHH`, Qingdao `ZSQD`, Taipei `RCSS`, Manila `RPLL`, Jakarta `WIHH`, London `EGLC`, Paris `LFPB`, Madrid `LEMD`, Warsaw `EPWA`, Helsinki `EFHK`, Moscow `UUWW`, Ankara `LTAC`, Tel Aviv `LLBG`, Cape Town `FACT`, Wellington `NZWN`, Buenos Aires `SAEZ`, São Paulo `SBGR`, Panama City `MPMG`.

Run `/cities` in the bot to see the full list, including AI-trap flags.

## Setup

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather), grab the token.
2. Find your numeric chat ID (DM the bot once, then `curl -s "https://api.telegram.org/bot$TOKEN/getUpdates"`).
3. Install:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure:
   ```bash
   cp .env.example .env
   # fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
   set -a && source .env && set +a
   ```
5. Run:
   ```bash
   python bot.py
   ```

No paid API keys required. Open-Meteo (forecasts) and Polymarket Gamma (markets) are both free and open.

## Commands

| Command | What it does |
|---|---|
| `/scan` | Top 10 confidence-ranked across all 35 cities, 0-3d out |
| `/scan all` | Full ranked list, no truncation |
| `/scan enter` | Only markets passing the entry gate |
| `/today` | Markets resolving today (per-city local) |
| `/upcoming` | Markets resolving in 1-3 days |
| `/forecast <city> [YYYY-MM-DD]` | Full card for one market |
| `/track <city> <YYYY-MM-DD>` | Start tracking a market through resolution |
| `/positions` | Tracked markets with confidence drift |
| `/refresh` | Re-score all tracked markets right now |
| `/untrack <city> <YYYY-MM-DD>` | Stop tracking |
| `/cities` | List all 35 cities |
| `/help` | Command list |

## Confidence scoring

Each market's confidence is `[0, 1]`, weighted from four components:

| Weight | Component | What it measures |
|---|---|---|
| 0.35 | `P(any of 3 buckets wins)` | ECMWF ENS members landing inside any of the 3 selected buckets |
| 0.25 | `model_agreement` | Fraction of deterministic models (ECMWF det, AIFS, GraphCast, +HRRR) whose forecast lands in the PRIMARY bucket |
| 0.20 | `ens_tightness` | `1 - normalized(ENS stdev)` — tighter ensemble → higher score |
| 0.20 | `entry_window` | Fraction of selected buckets priced in the 25-40¢ entry window |

Sizing tier off the final score:

| Confidence | Tier | Multiplier |
|---|---|---|
| ≥ 0.90 | MAX | 2.0× base unit |
| ≥ 0.80 | STRONG | 1.5× |
| ≥ 0.70 | BASE | 1.0× |
| ≥ 0.60 | PROBE | 0.5× |
| < 0.60 | SKIP | — |

Base unit defaults to `$1.00` per bucket position (so 3 buckets = $3 at BASE). Override with `POSITION_UNIT` env var.

## Refinement workflow (3-day → 1-day → 12h)

1. **3 days out**: Initial entry recommendation off ECMWF/AIFS/GraphCast medium-range. PROBE or BASE sizing typical.
2. **1 day out**: Hourly refresh, models converge. If confidence rises >10% → bot says ADD. If falls >10% → TRIM.
3. **12-24h out**: HRRR comes online for US cities. ECMWF short-range tightens for international. Bot fires "low collapse" alerts when a bucket's price drops out of the 25-40¢ window — that's the SL trigger and the adjacent-TP trigger from the methodology.
4. **Resolution**: Market closes, tracker auto-archives.

## Architecture

```
bot.py            ← Telegram entrypoint + command handlers + scheduler
scanner.py        ← parallel fan-out across all cities
strategy.py       ← 3-bucket selection + confidence scoring + SL/TP
forecast.py       ← Open-Meteo client (ECMWF det/ENS, AIFS, GraphCast, GFS, HRRR)
polymarket.py     ← Gamma API client + slug builder + bucket parser (F/C aware)
tracker.py        ← JSON-backed persistence for tracked positions
timeutil.py       ← per-station local-date resolution (the TZ guardrail)
cities.py         ← registry of all 35 cities + stations + units
config.py         ← thresholds, weights, sizing tiers
messages.py       ← Telegram message formatters
```

## Notes & caveats

- Some Wunderground paths are inferred from Polymarket's slug pattern + ICAO. If the bot reports an empty event for a slug it built, double-check `cities.py` — Polymarket occasionally uses an alternate path token, especially for European stations.
- Hong Kong is the one verified non-Wunderground exception (uses HK Observatory direct, 0.1°C precision). If you spot another non-WU source while running the bot, add a `resolution_source` to that city's entry in `cities.py` the same way.
- The bot persists tracked markets in a flat JSON file (`tracker.json`). Fine for single-user. Swap to SQLite if you run multi-user or need atomic concurrent writes.
- Open-Meteo is rate-limited at ~10K requests/day per IP for non-commercial use. A full `/scan` across 35 cities × 4 days is ~280 requests.
