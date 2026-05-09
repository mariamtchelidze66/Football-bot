import os
import json
import asyncio
import base64
import logging
import urllib.parse
import requests as _requests
from datetime import datetime, time as dt_time, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import anthropic

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
APISPORTS_KEY      = os.environ.get("APISPORTS_KEY", "")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── In-memory state ──────────────────────────────────────────────────────────

conversation_history: dict[int, list[dict]] = {}
subscriptions:        dict[int, set[str]]   = {}
last_scores:          dict[str, str]        = {}
match_cache:          dict[str, dict[str, dict]] = {}
CACHE_MAX_AGE_HOURS = 6

# ─── API-Sports configuration ─────────────────────────────────────────────────

APISPORTS_BASE = "https://v3.football.api-sports.io"
APISPORTS_HEADERS = {"x-apisports-key": APISPORTS_KEY}

# Internal league key → API-Sports league ID
APISPORTS_LEAGUE_IDS: dict[str, int] = {
    "england_pl":    39,
    "spain_laliga":  140,
    "italy_serie_a": 135,
    "germany_buli":   78,
    "france_ligue1":  61,
    "uefa_cl":         2,
    "uefa_el":         3,
    "uefa_conf":     848,
    "usa_mls":       253,
    "brazil_serie_a": 71,
}

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

LEAGUES: dict[str, str] = {
    "england_pl":    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",
    "spain_laliga":  "🇪🇸 La Liga",
    "italy_serie_a": "🇮🇹 Serie A",
    "germany_buli":  "🇩🇪 Bundesliga",
    "france_ligue1": "🇫🇷 Ligue 1",
    "uefa_cl":       "🌟 Champions League",
    "uefa_el":       "🟠 Europa League",
    "uefa_conf":     "⚪ Conference League",
    "usa_mls":       "🇺🇸 MLS",
    "brazil_serie_a":"🇧🇷 Brasileirão",
}

SCORE_UPDATE_INTERVAL = 30 * 60

# Serialises all Claude API requests so only one is in-flight at a time.
_queue_lock: asyncio.Lock | None = None

# ─── Season helper ────────────────────────────────────────────────────────────

def _current_season(league_key: str) -> int:
    """Return the current season year for a given league key.

    European leagues (Aug–May): season number = the calendar year the season started.
    Calendar-year leagues (MLS, Brasileirão): season = current calendar year.
    """
    now = datetime.now(timezone.utc)
    if league_key in ("usa_mls", "brazil_serie_a"):
        return now.year
    # European: before August we're still in the season that started the previous year
    return now.year - 1 if now.month < 8 else now.year

# ─── API-Sports helpers ───────────────────────────────────────────────────────

def _apisports_get_blocking(endpoint: str, params: dict) -> dict:
    """Blocking GET to API-Sports — run via asyncio.to_thread."""
    url = f"{APISPORTS_BASE}/{endpoint}"
    resp = _requests.get(url, headers=APISPORTS_HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    errors = data.get("errors", {})
    if errors and errors != [] and errors != {}:
        raise ValueError(f"API-Sports error on /{endpoint}: {errors}")
    return data


async def apisports_get(endpoint: str, params: dict) -> dict:
    return await asyncio.to_thread(_apisports_get_blocking, endpoint, params)


async def apisports_live_scores(league_id: int | None = None) -> list[dict]:
    """Fetch live/in-progress fixtures, optionally filtered by league."""
    if not APISPORTS_KEY:
        return []
    try:
        params: dict = {"live": "all"}
        if league_id:
            params["live"] = str(league_id)
        data = await apisports_get("fixtures", params)
        return data.get("response") or []
    except Exception as e:
        logger.warning("API-Sports livescores failed: %s", e)
        return []


async def apisports_fixtures(league_id: int, date: str, season: int) -> list[dict]:
    """Fetch fixtures for a league on a specific date."""
    if not APISPORTS_KEY:
        return []
    try:
        data = await apisports_get("fixtures", {
            "league": league_id,
            "season": season,
            "date": date,
        })
        return data.get("response") or []
    except Exception as e:
        logger.warning("API-Sports fixtures failed (league=%d): %s", league_id, e)
        return []


async def apisports_standings(league_id: int, season: int) -> list[dict]:
    """Return a flat list of standing rows for a league season."""
    if not APISPORTS_KEY:
        return []
    try:
        data = await apisports_get("standings", {"league": league_id, "season": season})
        items = data.get("response") or []
        if not items:
            return []
        standings_groups = items[0]["league"]["standings"]
        # Most leagues have one group; return first group's rows
        return standings_groups[0] if standings_groups else []
    except Exception as e:
        logger.warning("API-Sports standings failed (league=%d): %s", league_id, e)
        return []


async def apisports_lineups(fixture_id: int | str) -> list[dict]:
    """Return both teams' lineup dicts for a fixture."""
    if not APISPORTS_KEY:
        return []
    try:
        data = await apisports_get("fixtures/lineups", {"fixture": fixture_id})
        return data.get("response") or []
    except Exception as e:
        logger.warning("API-Sports lineups failed (fixture=%s): %s", fixture_id, e)
        return []


async def apisports_injuries(fixture_id: int | str) -> list[dict]:
    """Return injury/suspension list for a fixture."""
    if not APISPORTS_KEY:
        return []
    try:
        data = await apisports_get("injuries", {"fixture": fixture_id})
        return data.get("response") or []
    except Exception as e:
        logger.warning("API-Sports injuries failed (fixture=%s): %s", fixture_id, e)
        return []


async def apisports_fixture_by_id(fixture_id: int | str) -> dict | None:
    """Fetch a single fixture record (includes referee name, status, score)."""
    if not APISPORTS_KEY:
        return None
    try:
        data = await apisports_get("fixtures", {"id": fixture_id})
        items = data.get("response") or []
        return items[0] if items else None
    except Exception as e:
        logger.warning("API-Sports fixture lookup failed (id=%s): %s", fixture_id, e)
        return None

# ─── Score / standings formatters ─────────────────────────────────────────────

_LIVE_STATUSES  = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE"}
_FINAL_STATUSES = {"FT", "AET", "PEN"}


def _fixture_score_line(f: dict) -> tuple[str, str] | None:
    """Return (status_bucket, formatted_line) or None for upcoming matches."""
    fix    = f.get("fixture", {})
    teams  = f.get("teams", {})
    goals  = f.get("goals", {})
    status = fix.get("status", {})
    short  = (status.get("short") or "").upper()
    elapsed = status.get("elapsed")

    home = teams.get("home", {}).get("name", "?")
    away = teams.get("away", {}).get("name", "?")
    h_goals = goals.get("home")
    a_goals = goals.get("away")
    score = f"{h_goals} - {a_goals}" if h_goals is not None and a_goals is not None else "? - ?"

    if short in _LIVE_STATUSES:
        elapsed_str = f"{elapsed}'" if elapsed else short
        return ("live", f"🔴 {home} {score} {away}  ({elapsed_str})")
    if short in _FINAL_STATUSES:
        return ("ft", f"✅ {home} {score} {away}")
    return None


def format_apisports_scores(fixtures: list[dict]) -> str | None:
    """Format a list of API-Sports fixture dicts into a compact score summary."""
    live_lines, ft_lines = [], []
    for f in fixtures:
        result = _fixture_score_line(f)
        if result:
            bucket, line = result
            (live_lines if bucket == "live" else ft_lines).append(line)
    all_lines = live_lines + ft_lines
    return "\n".join(all_lines) if all_lines else None


def format_apisports_standings(rows: list[dict], league_name: str) -> str:
    """Format standings rows into a Markdown table."""
    if not rows:
        return f"No standings data available for {league_name}."
    header = f"📊 *{league_name} Standings* (Source: API-Sports)\n\n"
    header += "`Pos  Team                  P   W  D  L  GD  Pts  Form`\n"
    lines = []
    for row in rows:
        pos  = str(row.get("rank", "?")).ljust(4)
        team = str(row.get("team", {}).get("name", "?"))[:21].ljust(21)
        all_ = row.get("all", {})
        p    = str(all_.get("played", "?")).ljust(3)
        w    = str(all_.get("win", "?")).ljust(2)
        d    = str(all_.get("draw", "?")).ljust(2)
        l    = str(all_.get("lose", "?")).ljust(2)
        gd   = str(row.get("goalsDiff", "?")).ljust(4)
        pts  = str(row.get("points", "?")).ljust(4)
        form = str(row.get("form") or "")[-5:]  # last 5 results
        lines.append(f"`{pos} {team} {p} {w} {d} {l} {gd} {pts} {form}`")
    return header + "\n".join(lines)


def format_apisports_lineups(lineup_data: list[dict]) -> dict:
    """Convert API-Sports lineups response into a cache-friendly dict."""
    result: dict = {}
    for team_lu in lineup_data:
        team_name = team_lu.get("team", {}).get("name", "Unknown")
        formation = team_lu.get("formation", "N/A")
        start_xi  = [
            f"{p['player']['number']}. {p['player']['name']} ({p['player']['pos']})"
            for p in team_lu.get("startXI", [])
        ]
        subs = [
            f"{p['player']['number']}. {p['player']['name']} ({p['player']['pos']})"
            for p in team_lu.get("substitutes", [])
        ]
        result[team_name] = {
            "formation": formation,
            "startXI": start_xi,
            "substitutes": subs,
        }
    result["source"] = "API-Sports"
    return result


def format_apisports_injuries(injury_data: list[dict]) -> dict:
    """Convert API-Sports injuries response into a cache-friendly dict."""
    by_team: dict[str, list[str]] = {}
    for item in injury_data:
        player = item.get("player", {})
        team   = item.get("team", {}).get("name", "Unknown")
        reason = player.get("reason") or "Unknown reason"
        name   = player.get("name", "?")
        by_team.setdefault(team, []).append(f"{name} — {reason}")
    return {**by_team, "source": "API-Sports"}

# ─── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "CRITICAL RULE — READ THIS FIRST:\n"
    "You MUST use the web_search tool before writing ANY response that involves football facts, "
    "matches, scores, teams, players, injuries, lineups, standings, transfers, or news. "
    "NEVER ask the user for more details. NEVER say you need more information. "
    "NEVER answer from memory alone. If the user's question is about football, "
    "search immediately — even if the question seems vague. Make a reasonable search query "
    "based on what they asked and fetch the live data first. "
    "Only after searching may you write your response.\n\n"
    "API-SPORTS DATA RULE:\n"
    "When a user's message begins with '=== CACHED MATCH DATA: ...' this contains live structured "
    "data pre-fetched from the API-Sports API (v3.football.api-sports.io). "
    "This is your most reliable source for scores, fixtures, lineups, injuries, and standings — "
    "always cite it as '(Source: API-Sports)'. "
    "You may still use web_search to supplement it with referee stats, tactical analysis, "
    "transfer news, and any data not covered by the cached block.\n\n"
    "You are a knowledgeable football (soccer) assistant with real-time web search capability. "
    "You specialise in providing up-to-date football news, live scores, match results, "
    "injury updates, team news, transfer rumours, fixtures, standings, and player statistics. "
    "You cover all major leagues and competitions worldwide (Premier League, La Liga, "
    "Serie A, Bundesliga, Ligue 1, Champions League, World Cup, etc.). "
    "Present information in a clear, structured way. If scores or news are unavailable after searching, say so honestly.\n\n"
    "MANDATORY SOURCE CITATION RULE — THIS OVERRIDES EVERYTHING ELSE:\n"
    "Every single sentence in your response that contains a factual claim MUST end with the source "
    "in parentheses, for example: "
    "'Liverpool are 1st with 84 points (Source: API-Sports).' "
    "'Salah has scored 28 goals this season (Source: Sofascore).' "
    "'The match kicks off at 17:30 BST (Source: API-Sports).'\n"
    "This rule applies to EVERY sentence — standings, scores, statistics, injuries, lineups, "
    "tactical observations, transfer news, referee stats, weather, and all other facts.\n"
    "If you searched for a fact but cannot identify which specific source it came from, "
    "you MUST remove that sentence entirely from your response — do not include unsourced claims.\n"
    "You may only omit a source citation for sentences that contain no factual claim "
    "(e.g. transitional phrases like 'Here is a preview of the match:').\n"
    "Never group multiple facts under one source citation at the end of a paragraph — "
    "each individual sentence must carry its own citation.\n\n"
    "MATCH ANALYSIS RULE: Whenever you analyse a match (preview, review, or tactical breakdown), "
    "you MUST always include ALL of the following sections:\n\n"
    "SOURCE PRIORITY FOR MATCH STATISTICS:\n"
    "Always fetch match statistics in this strict priority order:\n"
    "1. API-Sports data (if pre-injected in the message as CACHED MATCH DATA)\n"
    "2. Sofascore (sofascore.com)\n"
    "3. FBref.com\n"
    "4. BBC Sport (bbc.com/sport)\n"
    "5. Sky Sports (skysports.com)\n"
    "You MUST state which source each statistic or data point came from.\n\n"
    "DISCIPLINE STATS (per team):\n"
    "1. Average yellow cards per game this season.\n"
    "2. Total yellow cards across their last 5 matches.\n\n"
    "REFEREE INFO:\n"
    "3. The appointed referee's full name — check the CACHED MATCH DATA block first; "
    "if not there, search for the fixture on Sofascore or the league's official site.\n"
    "4. The referee's average yellow cards per game this season.\n"
    "5. The referee's red card count this season.\n"
    "6. The referee's penalty decisions record this season.\n\n"
    "WEATHER FORECAST:\n"
    "7. Real-time weather for the match city on match day. "
    "Fetch in this order — try source 1 first, fall back to source 2 if it fails:\n"
    "  Source 1 (primary): https://wttr.in/{CITY}?format=j1 "
    "— parse temp_C, windspeedKmph, chanceofrain from the JSON.\n"
    "  Source 2 (fallback): Open-Meteo — first geocode the city at "
    "https://geocoding-api.open-meteo.com/v1/search?name={CITY}&count=1 to get lat/lon, "
    "then fetch https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON"
    "&current_weather=true&hourly=precipitation_probability.\n"
    "Always state which source the weather data came from. "
    "NEVER estimate or guess weather — always fetch live data.\n\n"
    "Present each section clearly with a heading. "
    "If any data point is unavailable after fetching, state that explicitly.\n\n"
    "PREDICTED LINEUP & TACTICAL ANALYSIS RULE:\n"
    "When providing predicted lineups or tactical analysis, search ONLY these four sources, in order:\n"
    "1. BBC Sport (bbc.com/sport)\n"
    "2. Sky Sports (skysports.com)\n"
    "3. The Athletic (theathletic.com)\n"
    "4. The club's official website\n"
    "For EACH team's predicted lineup write the source on the same line, "
    "e.g. 'Predicted XI (Source: Sky Sports)'. "
    "If confirmed lineups are in the CACHED MATCH DATA block, use those and cite '(Source: API-Sports)'.\n"
    "If no lineup is found on any trusted source, write: "
    "'Predicted lineup not available from trusted sources'\n\n"
    "IMAGE ANALYSIS RULE: When the user sends an image of a betslip or match statistics, "
    "carefully read all text, odds, teams, markets, and selections visible in the image. "
    "Then use web_search to look up current form, head-to-head records, injuries, "
    "and relevant statistics for the teams or events shown. "
    "Provide a structured betting insight covering: "
    "(1) a summary of the betslip/stats sheet, "
    "(2) value assessment for each selection, "
    "(3) key risk factors (injuries, suspensions, poor form), "
    "(4) overall recommendation on whether the bet represents good value. "
    "Be direct and analytical. Always note the inherent risk of gambling."
)

SCORES_PROMPT = (
    "You are a football scores reporter. Search the web for the VERY LATEST completed match results "
    "for the specified league TODAY or within the last 24 hours. "
    "Return ONLY a compact plain-text list of completed results in this exact format:\n"
    "Home Team X - Y Away Team\n"
    "One match per line, no extra commentary, no markdown. "
    "If no matches have been completed recently, reply with exactly: NO_RECENT_MATCHES"
)

FETCH_SYSTEM = (
    "You are a football data researcher with web search capability. "
    "Search the specified sources and return ONLY valid JSON — no markdown, no code fences, no commentary. "
    "For predicted lineups and tactical analysis use ONLY these four sources: "
    "BBC Sport (bbc.com/sport), Sky Sports (skysports.com), The Athletic (theathletic.com), "
    "or the club's official website. No other sources are permitted for lineups. "
    "For injury data use the same four sources. "
    "For statistics use: Sofascore, FBref.com, BBC Sport, Sky Sports. "
    "Always include a 'source' field naming the exact website the data came from. "
    "If lineup data cannot be found on any of the four trusted sources, set the value to "
    "'Predicted lineup not available from trusted sources'. "
    "If other data cannot be found, set the value to 'Could not find from trusted sources'."
)

# ─── Telegram keyboard ────────────────────────────────────────────────────────

def build_leagues_keyboard(user_id: int) -> InlineKeyboardMarkup:
    user_subs = subscriptions.get(user_id, set())
    buttons = []
    for key, name in LEAGUES.items():
        tick = "✅" if key in user_subs else "⬜"
        buttons.append([InlineKeyboardButton(f"{tick} {name}", callback_data=f"league_{key}")])
    buttons.append([InlineKeyboardButton("✔️ Done", callback_data="league_done")])
    return InlineKeyboardMarkup(buttons)

# ─── Command handlers ─────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚽ Hello! I'm a football assistant powered by Claude with live web search.\n\n"
        "I can help you with:\n"
        "• Latest match results & live scores\n"
        "• Team news & injury updates\n"
        "• Transfer rumours & signings\n"
        "• Fixtures & standings\n"
        "• Player stats & analysis\n\n"
        "Use /leagues to subscribe to automatic score updates.\n"
        "Use /clear to reset our conversation."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conversation_history.pop(user_id, None)
    await update.message.reply_text("Conversation cleared. Starting fresh!")


async def leagues_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    sub_count = len(subscriptions.get(user_id, set()))
    header = (
        f"🏆 *League Subscriptions*\n\n"
        f"Toggle leagues to receive automatic score updates every 30 minutes.\n"
        f"Currently subscribed to *{sub_count}* league(s).\n"
    )
    await update.message.reply_text(
        header,
        parse_mode="Markdown",
        reply_markup=build_leagues_keyboard(user_id),
    )


async def league_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data    = query.data

    if data == "league_done":
        user_subs = subscriptions.get(user_id, set())
        if user_subs:
            names = [LEAGUES[k] for k in user_subs if k in LEAGUES]
            await query.edit_message_text(
                f"✅ Subscribed to {len(names)} league(s):\n"
                + "\n".join(f"  • {n}" for n in names)
                + "\n\nYou'll receive score updates every 30 minutes when matches are played.\n"
                "Use /leagues to change your subscriptions."
            )
        else:
            await query.edit_message_text(
                "You have no active league subscriptions.\nUse /leagues to subscribe."
            )
        return

    if data.startswith("league_"):
        league_key = data[len("league_"):]
        if league_key not in LEAGUES:
            return
        subs = subscriptions.setdefault(user_id, set())
        if league_key in subs:
            subs.discard(league_key)
            logger.info("User %d unsubscribed from %s", user_id, league_key)
        else:
            subs.add(league_key)
            logger.info("User %d subscribed to %s", user_id, league_key)

        sub_count = len(subs)
        header = (
            f"🏆 *League Subscriptions*\n\n"
            f"Toggle leagues to receive automatic score updates every 30 minutes.\n"
            f"Currently subscribed to *{sub_count}* league(s).\n"
        )
        await query.edit_message_text(
            header,
            parse_mode="Markdown",
            reply_markup=build_leagues_keyboard(user_id),
        )

# ─── Score fetching (API-Sports primary → web search fallback) ────────────────

async def _fetch_league_scores_via_search(league_name: str) -> str | None:
    """Fallback: use Claude web search to get recent scores."""
    loop_messages = [{
        "role": "user",
        "content": (
            f"Search for the latest completed match results for {league_name} today "
            f"or in the last 24 hours (current UTC time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}). "
            "List only finished matches with their scores."
        ),
    }]
    while True:
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=SCORES_PROMPT,
            tools=[WEB_SEARCH_TOOL],
            messages=loop_messages,
        )
        if response.stop_reason == "tool_use":
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            loop_messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })
            loop_messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in tool_blocks
            ]})
        else:
            text = "\n".join(b.text for b in response.content if b.type == "text").strip()
            return None if (not text or "NO_RECENT_MATCHES" in text) else text


async def fetch_league_scores(league_key: str, league_name: str) -> str | None:
    """
    Fetch recent scores for a league.
    Primary: API-Sports (live + today's fixtures).
    Fallback: Claude web search.
    """
    if not APISPORTS_KEY:
        return await _fetch_league_scores_via_search(league_name)

    league_id = APISPORTS_LEAGUE_IDS.get(league_key)
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    season    = _current_season(league_key)

    if league_id:
        live, fixtures = await asyncio.gather(
            apisports_live_scores(league_id),
            apisports_fixtures(league_id, today, season),
        )
        # Merge: live takes priority; avoid duplicates by fixture id
        live_ids = {f["fixture"]["id"] for f in live}
        combined = live + [f for f in fixtures if f["fixture"]["id"] not in live_ids]
        scores = format_apisports_scores(combined)
        if scores:
            logger.info("API-Sports scores OK for %s (%d events)", league_key, len(combined))
            return scores
        logger.info("API-Sports returned no scores for %s — falling back to web search", league_key)

    return await _fetch_league_scores_via_search(league_name)


async def score_update_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    league_subscribers: dict[str, list[int]] = {}
    for user_id, user_leagues in subscriptions.items():
        for league_key in user_leagues:
            league_subscribers.setdefault(league_key, []).append(user_id)

    if not league_subscribers:
        return

    logger.info("Score update job: checking %d league(s)", len(league_subscribers))

    for league_key, subscriber_ids in league_subscribers.items():
        league_name = LEAGUES.get(league_key, league_key)
        try:
            scores_text = await fetch_league_scores(league_key, league_name)
            if scores_text is None:
                logger.info("No recent matches for %s", league_key)
                continue
            if last_scores.get(league_key) == scores_text:
                logger.info("No new results for %s", league_key)
                continue
            last_scores[league_key] = scores_text
            message = f"⚽ *{league_name} — Latest Results*\n\n{scores_text}"
            for user_id in subscriber_ids:
                try:
                    await context.bot.send_message(
                        chat_id=user_id, text=message, parse_mode="Markdown"
                    )
                    logger.info("Sent score update to user %d for %s", user_id, league_key)
                except Exception as e:
                    logger.error("Failed to send to user %d: %s", user_id, e)
        except Exception as e:
            logger.error("Error fetching scores for %s: %s", league_key, e)

# ─── Cache helpers ────────────────────────────────────────────────────────────

def _cache_key(home: str, away: str) -> str:
    return f"{home.strip().lower()}_vs_{away.strip().lower()}"


def is_cache_fresh(fetched_at_iso: str) -> bool:
    fetched_at = datetime.fromisoformat(fetched_at_iso)
    age = datetime.now(timezone.utc) - fetched_at
    return age.total_seconds() < CACHE_MAX_AGE_HOURS * 3600


def _wttr_blocking(city: str) -> dict:
    url  = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
    resp = _requests.get(url, headers={"User-Agent": "curl/7.0"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    cond = data["current_condition"][0]
    hourly = data.get("weather", [{}])[0].get("hourly", [{}])
    return {
        "temperature_c":  cond["temp_C"],
        "wind_kmph":      cond["windspeedKmph"],
        "rain_chance_pct": hourly[0].get("chanceofrain", "N/A") if hourly else "N/A",
        "description":    cond["weatherDesc"][0]["value"],
        "source":         "wttr.in",
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }


def _openmeteo_blocking(city: str) -> dict:
    geo = _requests.get(
        f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1",
        timeout=10,
    ).json()
    if not geo.get("results"):
        raise ValueError(f"Open-Meteo geocoding found no results for '{city}'")
    lat, lon = geo["results"][0]["latitude"], geo["results"][0]["longitude"]
    wx = _requests.get(
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&current_weather=true"
        f"&hourly=precipitation_probability&timezone=auto&forecast_days=1",
        timeout=10,
    ).json()
    cw         = wx["current_weather"]
    rain_chance = wx.get("hourly", {}).get("precipitation_probability", [None])[0]
    return {
        "temperature_c":  str(cw["temperature"]),
        "wind_kmph":      str(round(cw["windspeed"])),
        "rain_chance_pct": str(rain_chance) if rain_chance is not None else "N/A",
        "description":    f"WMO code {cw['weathercode']}",
        "source":         "Open-Meteo",
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }


async def fetch_weather(city: str) -> dict:
    try:
        return await asyncio.to_thread(_wttr_blocking, city)
    except Exception as e:
        logger.warning("wttr.in failed for '%s' (%s) — trying Open-Meteo", city, e)
    try:
        return await asyncio.to_thread(_openmeteo_blocking, city)
    except Exception as e2:
        logger.error("Both weather sources failed for '%s': %s", city, e2)
        return {"error": "Both wttr.in and Open-Meteo failed", "source": "none",
                "updated_at": datetime.now(timezone.utc).isoformat()}


async def claude_fetch_json(prompt: str) -> dict | list:
    """Run Claude + web search agentic loop, expecting a JSON response."""
    loop_msgs = [{"role": "user", "content": prompt}]
    while True:
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-sonnet-4-5",
            max_tokens=2048,
            system=FETCH_SYSTEM,
            tools=[WEB_SEARCH_TOOL],
            messages=loop_msgs,
        )
        if response.stop_reason == "tool_use":
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            loop_msgs.append({"role": "assistant",
                               "content": [b.model_dump() for b in response.content]})
            loop_msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in tool_blocks
            ]})
        else:
            text = "\n".join(b.text for b in response.content if b.type == "text").strip()
            if "```" in text:
                parts = text.split("```")
                text = (parts[1][4:] if parts[1].startswith("json") else parts[1]).strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                logger.warning("claude_fetch_json: JSON parse failed: %s", text[:200])
                return {"parse_error": True, "raw": text,
                        "updated_at": datetime.now(timezone.utc).isoformat()}

# ─── Fixture fetching (API-Sports primary → web search fallback) ──────────────

async def _fetch_pl_fixtures_via_search() -> list[dict]:
    today  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await claude_fetch_json(
        f"Search premierleague.com and BBC Sport for ALL Premier League matches "
        f"scheduled for today ({today}). Return a JSON array where each element has: "
        '"home_team", "away_team", "kickoff_utc", "stadium", "city". '
        "If there are no matches today, return an empty array []."
    )
    if isinstance(result, list):
        return result
    for key in ("matches", "fixtures", "games"):
        if isinstance(result, dict) and isinstance(result.get(key), list):
            return result[key]  # type: ignore
    return []


async def fetch_pl_fixtures_today() -> list[dict]:
    """
    Fetch today's Premier League fixtures.
    Primary: API-Sports.  Fallback: Claude web search.
    """
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    season    = _current_season("england_pl")
    league_id = APISPORTS_LEAGUE_IDS["england_pl"]

    if APISPORTS_KEY:
        raw = await apisports_fixtures(league_id, today, season)
        if raw:
            return [
                {
                    "home_team":    f["teams"]["home"]["name"],
                    "away_team":    f["teams"]["away"]["name"],
                    "kickoff_utc":  f["fixture"]["date"],
                    "stadium":      f["fixture"]["venue"].get("name", ""),
                    "city":         f["fixture"]["venue"].get("city", ""),
                    "fixture_id":   f["fixture"]["id"],
                    "referee":      f["fixture"].get("referee") or "",
                }
                for f in raw
            ]
        logger.info("API-Sports returned no PL fixtures today — falling back to web search")

    return await _fetch_pl_fixtures_via_search()

# ─── Match detail fetching ────────────────────────────────────────────────────

async def fetch_match_details(home: str, away: str, city: str, kickoff: str,
                               fixture_id: int | str | None = None) -> dict:
    """
    Fetch lineups, injuries, referee, discipline stats, and weather for one match.
    Structured data (lineups, injuries, referee name): API-Sports.
    Referee stats, discipline stats: Claude web search.
    Weather: wttr.in / Open-Meteo.
    """
    today   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    referee_stats_prompt = (
        f"Search Sofascore and FBref.com for detailed referee statistics for the referee of "
        f"{home} vs {away} on {today}. "
        "Return a JSON object with: "
        '"referee_name" (string), '
        '"yellows_per_game_season" (number or string), '
        '"red_cards_season" (number or string), '
        '"penalties_season" (number or string), '
        '"source" (string — exact website name).'
    )
    discipline_prompt = (
        f"Search Sofascore or FBref.com for yellow card discipline stats this season for "
        f"{home} and {away}. "
        "Return a JSON object with: "
        '"home_avg_yellows_per_game" (number), '
        '"home_yellows_last_5" (number), '
        '"away_avg_yellows_per_game" (number), '
        '"away_yellows_last_5" (number), '
        '"source" (string).'
    )

    # Kick off all fetches in parallel
    tasks = [
        fetch_weather(city),
        claude_fetch_json(referee_stats_prompt),
        claude_fetch_json(discipline_prompt),
    ]
    if fixture_id and APISPORTS_KEY:
        tasks += [
            apisports_lineups(fixture_id),
            apisports_injuries(fixture_id),
            apisports_fixture_by_id(fixture_id),
        ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    weather        = results[0] if not isinstance(results[0], Exception) else {"error": str(results[0]), "source": "none"}
    referee_stats  = results[1] if not isinstance(results[1], Exception) else {}
    disc_stats     = results[2] if not isinstance(results[2], Exception) else {}
    lineups_raw    = results[3] if len(results) > 3 and not isinstance(results[3], Exception) else []
    injuries_raw   = results[4] if len(results) > 4 and not isinstance(results[4], Exception) else []
    fixture_data   = results[5] if len(results) > 5 and not isinstance(results[5], Exception) else None

    lineups  = format_apisports_lineups(lineups_raw) if lineups_raw else {"note": "Not yet available", "source": "N/A"}
    injuries = format_apisports_injuries(injuries_raw) if injuries_raw else {"note": "None reported", "source": "API-Sports"}

    # Extract referee name from fixture data if available
    referee_name_api = ""
    if fixture_data and isinstance(fixture_data, dict):
        referee_name_api = fixture_data.get("fixture", {}).get("referee") or ""

    if not isinstance(referee_stats, dict):
        referee_stats = {}
    if referee_name_api and not referee_stats.get("referee_name"):
        referee_stats["referee_name"] = referee_name_api
        referee_stats.setdefault("source", "API-Sports (name) + web search (stats)")

    if not isinstance(disc_stats, dict):
        disc_stats = {}

    return {
        "home_team":   home,
        "away_team":   away,
        "city":        city,
        "kickoff_utc": kickoff,
        "fixture_id":  fixture_id,
        "fetched_at":  now_iso,
        "lineups":     lineups,
        "injuries":    injuries,
        "referee":     referee_stats or {"note": "Not available", "source": "N/A"},
        "home_discipline": {
            "avg_yellows_per_game": disc_stats.get("home_avg_yellows_per_game", "N/A"),
            "yellows_last_5":       disc_stats.get("home_yellows_last_5", "N/A"),
            "source":               disc_stats.get("source", "N/A"),
        },
        "away_discipline": {
            "avg_yellows_per_game": disc_stats.get("away_avg_yellows_per_game", "N/A"),
            "yellows_last_5":       disc_stats.get("away_yellows_last_5", "N/A"),
            "source":               disc_stats.get("source", "N/A"),
        },
        "weather": weather,
    }


async def morning_cache_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """7am UTC daily job: pre-fetch and cache all Premier League match data."""
    logger.info("Morning cache job: fetching today's PL fixtures")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        fixtures = await fetch_pl_fixtures_today()
    except Exception as e:
        logger.error("Morning cache job: failed to fetch fixtures: %s", e)
        return

    if not fixtures:
        logger.info("Morning cache job: no PL matches today")
        return

    logger.info("Morning cache job: %d fixture(s) found, fetching details", len(fixtures))
    match_cache.setdefault(today, {})

    for fx in fixtures:
        home       = fx.get("home_team", "").strip()
        away       = fx.get("away_team", "").strip()
        city       = fx.get("city", "").strip()
        kickoff    = fx.get("kickoff_utc", "TBD")
        fixture_id = fx.get("fixture_id")
        if not home or not away:
            continue
        try:
            key = _cache_key(home, away)
            logger.info("Morning cache: fetching %s vs %s (fixture_id=%s)", home, away, fixture_id)
            match_cache[today][key] = await fetch_match_details(home, away, city, kickoff, fixture_id)
            logger.info("Morning cache: cached %s vs %s", home, away)
        except Exception as e:
            logger.error("Morning cache: failed for %s vs %s: %s", home, away, e)


def find_cached_match(user_text: str) -> dict | None:
    today        = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_matches = match_cache.get(today, {})
    if not today_matches:
        return None
    text_lower = user_text.lower()
    for data in today_matches.values():
        home = data.get("home_team", "").lower()
        away = data.get("away_team", "").lower()
        if (home and home in text_lower) or (away and away in text_lower):
            if is_cache_fresh(data.get("fetched_at", "")):
                return data
    return None


def _fmt_section(title: str, content, fetched_at: str) -> str:
    if isinstance(content, dict):
        source = content.get("source", "N/A")
        lines  = [f"[CACHED — {title}] (Source: {source}, Last updated: {fetched_at})"]
        for k, v in content.items():
            if k != "source":
                if isinstance(v, list):
                    lines.append(f"  {k}:")
                    lines += [f"    - {item}" for item in v]
                else:
                    lines.append(f"  {k}: {v}")
        return "\n".join(lines)
    return f"[CACHED — {title}] {content} (Last updated: {fetched_at})"


def format_cache_context(data: dict) -> str:
    home       = data.get("home_team", "Home")
    away       = data.get("away_team", "Away")
    fetched_at = data.get("fetched_at", "unknown")
    kickoff    = data.get("kickoff_utc", "TBD")
    fixture_id = data.get("fixture_id", "N/A")
    weather    = data.get("weather", {})

    weather_line = (
        f"  Temperature: {weather.get('temperature_c')}°C, "
        f"Wind: {weather.get('wind_kmph')} km/h, "
        f"Rain chance: {weather.get('rain_chance_pct')}%, "
        f"Conditions: {weather.get('description')}"
    )

    return "\n".join([
        f"=== CACHED MATCH DATA: {home} vs {away} "
        f"(Kickoff: {kickoff}, API-Sports fixture_id: {fixture_id}) ===",
        f"Pre-fetched at 7am UTC — Last updated: {fetched_at}",
        "",
        f"[CACHED — WEATHER] (Source: {weather.get('source', 'wttr.in')}, Last updated: {fetched_at})",
        weather_line,
        "",
        _fmt_section("LINEUPS",          data.get("lineups",           {}), fetched_at),
        "",
        _fmt_section("INJURIES",         data.get("injuries",          {}), fetched_at),
        "",
        _fmt_section("REFEREE",          data.get("referee",           {}), fetched_at),
        "",
        _fmt_section("HOME DISCIPLINE",  data.get("home_discipline",   {}), fetched_at),
        "",
        _fmt_section("AWAY DISCIPLINE",  data.get("away_discipline",   {}), fetched_at),
        "",
        "=== END CACHED DATA — supplement with live search if needed ===",
    ])

# ─── Typing indicator ─────────────────────────────────────────────────────────

async def keep_typing(chat_id: int, bot) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass

# ─── Message handlers ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id   = update.effective_user.id
    user_text = update.message.text

    conversation_history.setdefault(user_id, [])

    cached = find_cached_match(user_text)
    if cached:
        enriched = f"{format_cache_context(cached)}\n\nUser question: {user_text}"
        conversation_history[user_id].append({"role": "user", "content": enriched})
        logger.info("Cache hit for user %d: %s vs %s",
                    user_id, cached.get("home_team"), cached.get("away_team"))
    else:
        conversation_history[user_id].append({"role": "user", "content": user_text})

    typing_task = asyncio.create_task(keep_typing(update.effective_chat.id, context.bot))
    try:
        async with _queue_lock:
            assistant_text = await run_agent_loop(user_id, update, context)
            conversation_history[user_id][-1] = {"role": "user", "content": user_text}
            conversation_history[user_id].append({"role": "assistant", "content": assistant_text})
            if len(conversation_history[user_id]) > 40:
                conversation_history[user_id] = conversation_history[user_id][-40:]
        await send_reply(update, assistant_text)
    except anthropic.RateLimitError:
        logger.error("Rate limit exhausted for user %d", user_id)
        await update.message.reply_text(
            "Sorry, Claude is overloaded right now. Please try again in a few minutes."
        )
    except anthropic.APIError as e:
        logger.error("Anthropic API error: %s", e)
        await update.message.reply_text(
            "Sorry, I encountered an error communicating with Claude. Please try again."
        )
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        await update.message.reply_text("An unexpected error occurred. Please try again.")
    finally:
        typing_task.cancel()


async def run_agent_loop(
    user_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str:
    loop_messages = list(conversation_history[user_id])
    while True:
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    client.messages.create,
                    model="claude-sonnet-4-5",
                    max_tokens=8192,
                    system=SYSTEM_PROMPT,
                    tools=[WEB_SEARCH_TOOL],
                    messages=loop_messages,
                )
                break
            except anthropic.RateLimitError:
                if attempt == 2:
                    raise
                logger.warning("Rate limited — waiting 60 s (attempt %d/2)", attempt + 1)
                if attempt == 0:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="⏳ Claude is rate limited — retrying in 60 seconds, please hang on…"
                    )
                await asyncio.sleep(60)
        else:
            raise RuntimeError("Exhausted retries")

        logger.info("stop_reason=%s content_types=%s", response.stop_reason,
                    [b.type for b in response.content])

        if response.stop_reason == "tool_use":
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            for blk in tool_blocks:
                logger.info("Web search: %s", blk.input.get("query", ""))
            loop_messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })
            loop_messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in tool_blocks
            ]})
        else:
            return ("\n".join(b.text for b in response.content if b.type == "text").strip()
                    or "I couldn't find any information on that.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    caption = update.message.caption or "Analyse this image and provide detailed betting insights."
    typing_task = asyncio.create_task(keep_typing(update.effective_chat.id, context.bot))
    try:
        photo      = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        photo_b64   = base64.b64encode(photo_bytes).decode("utf-8")

        conversation_history.setdefault(user_id, [])
        conversation_history[user_id].append({
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}},
                {"type": "text", "text": caption},
            ],
        })

        async with _queue_lock:
            assistant_text = await run_agent_loop(user_id, update, context)
            conversation_history[user_id][-1] = {
                "role": "user", "content": f"[Image sent] {caption}"
            }
            conversation_history[user_id].append({"role": "assistant", "content": assistant_text})
            if len(conversation_history[user_id]) > 40:
                conversation_history[user_id] = conversation_history[user_id][-40:]

        await send_reply(update, assistant_text)
    except anthropic.RateLimitError:
        await update.message.reply_text(
            "Sorry, Claude is overloaded right now. Please try again in a few minutes."
        )
    except anthropic.APIError as e:
        logger.error("Anthropic API error processing photo: %s", e)
        await update.message.reply_text(
            "Sorry, I encountered an error analysing that image. Please try again."
        )
    except Exception as e:
        logger.error("Unexpected error processing photo: %s", e)
        await update.message.reply_text("Sorry, I couldn't process that image. Please try again.")
    finally:
        typing_task.cancel()


async def send_reply(update: Update, text: str) -> None:
    MAX_LEN = 4096
    for i in range(0, len(text), MAX_LEN):
        await update.message.reply_text(text[i:i + MAX_LEN])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update %s caused error: %s", update, context.error)

# ─── Health server ────────────────────────────────────────────────────────────

HEALTH_PORT = 8765


async def health_server() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(1024)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK"
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", HEALTH_PORT)
    logger.info("Health server listening on port %d", HEALTH_PORT)
    async with server:
        await server.serve_forever()


async def post_init(application: Application) -> None:
    global _queue_lock
    _queue_lock = asyncio.Lock()
    asyncio.create_task(health_server())
    if APISPORTS_KEY:
        logger.info("API-Sports key present — structured data source ACTIVE (Pro plan)")
    else:
        logger.warning("APISPORTS_KEY not set — falling back to web search for all data")

# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("leagues", leagues_command))
    app.add_handler(CallbackQueryHandler(league_callback, pattern=r"^league_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)

    app.job_queue.run_repeating(score_update_job, interval=SCORE_UPDATE_INTERVAL, first=60)
    app.job_queue.run_daily(
        morning_cache_job,
        time=dt_time(hour=7, minute=0, tzinfo=timezone.utc),
    )

    logger.info(
        "Bot starting — API-Sports %s",
        "ACTIVE" if APISPORTS_KEY else "NOT CONFIGURED (web search fallback only)"
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
