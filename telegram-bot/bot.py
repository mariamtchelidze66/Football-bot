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
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

conversation_history: dict[int, list[dict]] = {}

subscriptions: dict[int, set[str]] = {}

last_scores: dict[str, str] = {}

match_cache: dict[str, dict[str, dict]] = {}
CACHE_MAX_AGE_HOURS = 6

PL_TEAMS = [
    "arsenal", "aston villa", "bournemouth", "brentford", "brighton",
    "chelsea", "crystal palace", "everton", "fulham", "ipswich",
    "leicester", "liverpool", "manchester city", "man city",
    "manchester united", "man united", "man utd", "newcastle",
    "nottingham forest", "forest", "southampton", "tottenham", "spurs",
    "west ham", "wolves", "wolverhampton",
]

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

LEAGUES: dict[str, str] = {
    "england_pl":       "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",
    "spain_laliga":     "🇪🇸 La Liga",
    "italy_serie_a":    "🇮🇹 Serie A",
    "germany_buli":     "🇩🇪 Bundesliga",
    "france_ligue1":    "🇫🇷 Ligue 1",
    "uefa_cl":          "🌟 Champions League",
    "uefa_el":          "🟠 Europa League",
    "uefa_conf":        "⚪ Conference League",
    "usa_mls":          "🇺🇸 MLS",
    "brazil_serie_a":   "🇧🇷 Brasileirão",
}

SCORE_UPDATE_INTERVAL = 30 * 60

# Serialises all Claude API requests so only one is in-flight at a time.
# Initialised inside the bot's event loop via post_init.
_queue_lock: asyncio.Lock | None = None

SYSTEM_PROMPT = (
    "CRITICAL RULE — READ THIS FIRST:\n"
    "You MUST use the web_search tool before writing ANY response that involves football facts, "
    "matches, scores, teams, players, injuries, lineups, standings, transfers, or news. "
    "NEVER ask the user for more details. NEVER say you need more information. "
    "NEVER answer from memory alone. If the user's question is about football, "
    "search immediately — even if the question seems vague. Make a reasonable search query "
    "based on what they asked and fetch the live data first. "
    "Only after searching may you write your response.\n\n"
    "You are a knowledgeable football (soccer) assistant with real-time web search capability. "
    "You specialise in providing up-to-date football news, live scores, match results, "
    "injury updates, team news, transfer rumours, fixtures, standings, and player statistics. "
    "You cover all major leagues and competitions worldwide (Premier League, La Liga, "
    "Serie A, Bundesliga, Ligue 1, Champions League, World Cup, etc.). "
    "Present information in a clear, structured way. If scores or news are unavailable after searching, say so honestly.\n\n"
    "MANDATORY SOURCE CITATION RULE — THIS OVERRIDES EVERYTHING ELSE:\n"
    "Every single sentence in your response that contains a factual claim MUST end with the source "
    "in parentheses, for example: "
    "'Liverpool are 4th with 58 points (Source: Premier League official website).' "
    "'Salah has scored 22 goals this season (Source: Sofascore).' "
    "'The match kicks off at 17:30 BST (Source: BBC Sport).'\n"
    "This rule applies to EVERY sentence — standings, scores, statistics, injuries, lineups, "
    "tactical observations, transfer news, referee stats, weather, and all other facts.\n"
    "If you searched for a fact but cannot identify which specific source it came from, "
    "you MUST remove that sentence entirely from your response — do not include unsourced claims.\n"
    "You may only omit a source citation for sentences that contain no factual claim "
    "(e.g. transitional phrases like 'Here is a preview of the match:').\n"
    "Never group multiple facts under one source citation at the end of a paragraph — "
    "each individual sentence must carry its own citation.\n\n"
    "MATCH ANALYSIS RULE: Whenever you analyse a match (preview, review, or tactical breakdown), "
    "you MUST always include ALL of the following sections, each sourced via web search:\n\n"
    "SOURCE PRIORITY FOR MATCH STATISTICS:\n"
    "Always fetch match statistics in this strict priority order, moving to the next only if the previous is unavailable:\n"
    "1. Sofascore (search for the match on sofascore.com)\n"
    "2. FBref.com (search fbref.com for the teams/match)\n"
    "3. BBC Sport (search bbc.com/sport)\n"
    "4. Sky Sports (search skysports.com)\n"
    "You MUST state which source each statistic or data point came from, e.g. '(Source: Sofascore)' or '(Source: FBref.com)'.\n\n"
    "DISCIPLINE STATS (per team):\n"
    "1. Average yellow cards per game this season.\n"
    "2. Total yellow cards across their last 5 matches.\n\n"
    "REFEREE INFO:\n"
    "3. The appointed referee's full name.\n"
    "4. The referee's average yellow cards per game this season.\n"
    "5. The referee's red card count this season.\n"
    "6. The referee's penalty decisions record this season (penalties awarded per game or total).\n\n"
    "WEATHER FORECAST:\n"
    "7. Real-time weather for the match city on match day. "
    "Fetch in this order — try source 1 first, fall back to source 2 if it fails:\n"
    "  Source 1 (primary): https://wttr.in/{CITY}?format=j1 "
    "— parse temp_C, windspeedKmph, chanceofrain from the JSON. "
    "  Source 2 (fallback): Open-Meteo — first geocode the city at "
    "https://geocoding-api.open-meteo.com/v1/search?name={CITY}&count=1 to get lat/lon, "
    "then fetch https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON&current_weather=true"
    "&hourly=precipitation_probability and read temperature, windspeed, precipitation_probability.\n"
    "Always state which source the weather data came from, e.g. '(Source: wttr.in)' or '(Source: Open-Meteo)'.\n"
    "NEVER estimate or guess weather — always fetch live data from one of these two URLs.\n\n"
    "Present each section clearly with a heading. "
    "If any data point is unavailable after fetching, state that explicitly rather than omitting the section.\n\n"
    "CACHE RULE: When a user's message begins with a block marked '=== CACHED MATCH DATA: ...' "
    "this is pre-fetched data retrieved at 7am — treat it as your primary source. "
    "Present the cached data clearly in your response, always noting the source and when it was last updated. "
    "You may supplement with live web searches for anything not covered or if the user asks for latest updates. "
    "If a cached field says 'Could not find from trusted sources', acknowledge that in your response.\n\n"
    "PREDICTED LINEUP & TACTICAL ANALYSIS RULE:\n"
    "When providing predicted lineups or any tactical analysis, you MUST search ONLY these four sources, in this order:\n"
    "1. BBC Sport (bbc.com/sport)\n"
    "2. Sky Sports (skysports.com)\n"
    "3. The Athletic (theathletic.com)\n"
    "4. The club's official website (e.g. manutd.com, arsenal.com, liverpoolfc.com, etc.)\n"
    "No other source is permitted for lineup predictions or tactical breakdowns — not Reddit, WhoScored, Transfermarkt, or any other site.\n"
    "For EACH team's predicted lineup, you MUST write the exact source it came from on the same line, "
    "e.g. 'Predicted XI (Source: Sky Sports)' or 'Predicted XI (Source: BBC Sport)'.\n"
    "If a predicted lineup cannot be found on ANY of these four sources after searching all of them, "
    "you MUST write exactly: 'Predicted lineup not available from trusted sources' — do not guess, "
    "do not use any other source, and do not fabricate a lineup.\n"
    "The same four-source rule applies to injury news cited within a tactical analysis.\n\n"
    "IMAGE ANALYSIS RULE: When the user sends an image of a betslip or match statistics, "
    "carefully read all text, odds, teams, markets, and selections visible in the image. "
    "Then use your web search tool to look up current form, head-to-head records, injuries, "
    "and any relevant statistics for the teams or events shown. "
    "Provide a structured betting insight that covers: "
    "(1) a summary of what the betslip or stats sheet contains, "
    "(2) value assessment for each selection based on current odds vs your probability estimate, "
    "(3) key risk factors such as injuries, suspensions, or poor recent form, "
    "(4) an overall recommendation on whether the bet represents good value. "
    "Be direct and analytical. Do not encourage reckless gambling — always note the inherent risk."
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


def build_leagues_keyboard(user_id: int) -> InlineKeyboardMarkup:
    user_subs = subscriptions.get(user_id, set())
    buttons = []
    for key, name in LEAGUES.items():
        tick = "✅" if key in user_subs else "⬜"
        buttons.append([InlineKeyboardButton(f"{tick} {name}", callback_data=f"league_{key}")])
    buttons.append([InlineKeyboardButton("✔️ Done", callback_data="league_done")])
    return InlineKeyboardMarkup(buttons)


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
    user_subs = subscriptions.get(user_id, set())
    sub_count = len(user_subs)
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
    data = query.data

    if data == "league_done":
        user_subs = subscriptions.get(user_id, set())
        if user_subs:
            names = [LEAGUES[k] for k in user_subs if k in LEAGUES]
            await query.edit_message_text(
                f"✅ Subscribed to {len(names)} league(s):\n" + "\n".join(f"  • {n}" for n in names) +
                "\n\nYou'll receive score updates every 30 minutes when matches are played.\n"
                "Use /leagues to change your subscriptions."
            )
        else:
            await query.edit_message_text(
                "You have no active league subscriptions.\n"
                "Use /leagues to subscribe."
            )
        return

    if data.startswith("league_"):
        league_key = data[len("league_"):]
        if league_key not in LEAGUES:
            return

        if user_id not in subscriptions:
            subscriptions[user_id] = set()

        if league_key in subscriptions[user_id]:
            subscriptions[user_id].discard(league_key)
            logger.info("User %d unsubscribed from %s", user_id, league_key)
        else:
            subscriptions[user_id].add(league_key)
            logger.info("User %d subscribed to %s", user_id, league_key)

        user_subs = subscriptions.get(user_id, set())
        sub_count = len(user_subs)
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


async def fetch_league_scores(league_key: str, league_name: str) -> str | None:
    messages = [{
        "role": "user",
        "content": (
            f"Search for the latest completed match results for {league_name} today "
            f"or in the last 24 hours (current UTC time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}). "
            "List only finished matches with their scores."
        ),
    }]

    loop_messages = list(messages)

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
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            loop_messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in tool_use_blocks
            ]
            loop_messages.append({"role": "user", "content": tool_results})
        else:
            text_blocks = [b.text for b in response.content if b.type == "text"]
            result = "\n".join(text_blocks).strip()
            if not result or "NO_RECENT_MATCHES" in result:
                return None
            return result


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
                        chat_id=user_id,
                        text=message,
                        parse_mode="Markdown",
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
    """Primary weather fetch from wttr.in — blocking, run via asyncio.to_thread."""
    url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
    resp = _requests.get(url, headers={"User-Agent": "curl/7.0"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    cond = data["current_condition"][0]
    today_weather = data.get("weather", [{}])[0]
    hourly = today_weather.get("hourly", [{}])
    rain_chance = hourly[0].get("chanceofrain", "N/A") if hourly else "N/A"
    return {
        "temperature_c": cond["temp_C"],
        "wind_kmph": cond["windspeedKmph"],
        "rain_chance_pct": rain_chance,
        "description": cond["weatherDesc"][0]["value"],
        "source": "wttr.in",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _openmeteo_blocking(city: str) -> dict:
    """Fallback weather fetch from Open-Meteo — blocking, run via asyncio.to_thread."""
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1"
    geo = _requests.get(geo_url, timeout=10).json()
    results = geo.get("results")
    if not results:
        raise ValueError(f"Open-Meteo geocoding found no results for '{city}'")
    lat, lon = results[0]["latitude"], results[0]["longitude"]

    wx_url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&current_weather=true"
        f"&hourly=precipitation_probability&timezone=auto&forecast_days=1"
    )
    wx = _requests.get(wx_url, timeout=10).json()
    cw = wx["current_weather"]
    rain_chance = wx.get("hourly", {}).get("precipitation_probability", [None])[0]
    return {
        "temperature_c": str(cw["temperature"]),
        "wind_kmph": str(round(cw["windspeed"])),
        "rain_chance_pct": str(rain_chance) if rain_chance is not None else "N/A",
        "description": f"WMO code {cw['weathercode']}",
        "source": "Open-Meteo",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def fetch_weather(city: str) -> dict:
    """Try wttr.in first; fall back to Open-Meteo if it fails."""
    try:
        return await asyncio.to_thread(_wttr_blocking, city)
    except Exception as e:
        logger.warning("wttr.in failed for '%s' (%s) — trying Open-Meteo", city, e)
    try:
        result = await asyncio.to_thread(_openmeteo_blocking, city)
        logger.info("Open-Meteo weather OK for '%s'", city)
        return result
    except Exception as e2:
        logger.error("Both weather sources failed for '%s': %s", city, e2)
        return {
            "error": "Both wttr.in and Open-Meteo failed",
            "source": "none",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


async def claude_fetch_json(prompt: str) -> dict | list:
    """Run Claude + web search agentic loop expecting a JSON response."""
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
            loop_msgs.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })
            loop_msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in tool_blocks
            ]})
        else:
            text = "\n".join(b.text for b in response.content if b.type == "text").strip()
            if "```" in text:
                parts = text.split("```")
                text = parts[1] if len(parts) > 1 else parts[0]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                logger.warning("claude_fetch_json: JSON parse failed: %s", text[:200])
                return {"parse_error": True, "raw": text,
                        "updated_at": datetime.now(timezone.utc).isoformat()}


async def fetch_pl_fixtures_today() -> list[dict]:
    """Fetch today's Premier League fixtures as a list of match dicts."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await claude_fetch_json(
        f"Search premierleague.com and BBC Sport for ALL Premier League matches "
        f"scheduled for today ({today}). Return a JSON array where each element has: "
        '"home_team", "away_team", "kickoff_utc", "stadium", "city". '
        "If there are no matches today, return an empty array []."
    )
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("matches", "fixtures", "games"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


async def fetch_match_details(home: str, away: str, city: str, kickoff: str) -> dict:
    """Fetch lineups, injuries, referee stats, and card stats for one match."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()

    details_prompt = (
        f"Search for detailed information about {home} vs {away} on {today} "
        f"(kickoff {kickoff}). Search BBC Sport, Sky Sports, The Athletic, "
        f"premierleague.com, Sofascore, and FBref.com. "
        "Return a single JSON object with exactly these fields:\n"
        '"lineups": expected/confirmed lineups for both teams (source field required)\n'
        '"injuries": injury and suspension lists for both teams (source field required, '
        'use trusted sources only: BBC Sport, Sky Sports, The Athletic, premierleague.com, club sites)\n'
        '"referee": {"name", "yellows_per_game_season", "red_cards_season", "penalties_season", "source"}\n'
        '"home_discipline": {"avg_yellows_per_game", "yellows_last_5_matches", "source"}\n'
        '"away_discipline": {"avg_yellows_per_game", "yellows_last_5_matches", "source"}\n'
    )

    details, weather = await asyncio.gather(
        claude_fetch_json(details_prompt),
        fetch_weather(city),
    )

    if not isinstance(details, dict):
        details = {}

    return {
        "home_team": home,
        "away_team": away,
        "city": city,
        "kickoff_utc": kickoff,
        "fetched_at": now_iso,
        "lineups": details.get("lineups", {"note": "Not available", "source": "N/A"}),
        "injuries": details.get("injuries", {"note": "Could not find from trusted sources", "source": "N/A"}),
        "referee": details.get("referee", {"note": "Not available", "source": "N/A"}),
        "home_discipline": details.get("home_discipline", {"note": "Not available", "source": "N/A"}),
        "away_discipline": details.get("away_discipline", {"note": "Not available", "source": "N/A"}),
        "weather": weather,
    }


async def morning_cache_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """7am daily job: pre-fetch and cache all Premier League match data."""
    logger.info("Morning cache job: fetching today's Premier League fixtures")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        fixtures = await fetch_pl_fixtures_today()
    except Exception as e:
        logger.error("Morning cache job: failed to fetch fixtures: %s", e)
        return

    if not fixtures:
        logger.info("Morning cache job: no PL matches today")
        return

    logger.info("Morning cache job: found %d fixture(s), fetching details", len(fixtures))
    match_cache.setdefault(today, {})

    for fixture in fixtures:
        home = fixture.get("home_team", "").strip()
        away = fixture.get("away_team", "").strip()
        city = fixture.get("city", "").strip()
        kickoff = fixture.get("kickoff_utc", "TBD")
        if not home or not away:
            continue
        try:
            key = _cache_key(home, away)
            logger.info("Morning cache job: fetching %s vs %s", home, away)
            match_cache[today][key] = await fetch_match_details(home, away, city, kickoff)
            logger.info("Morning cache job: cached %s vs %s", home, away)
        except Exception as e:
            logger.error("Morning cache job: failed for %s vs %s: %s", home, away, e)


def find_cached_match(user_text: str) -> dict | None:
    """Return fresh cached match data if the user mentions one of today's matches."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_matches = match_cache.get(today, {})
    if not today_matches:
        return None
    text_lower = user_text.lower()
    for data in today_matches.values():
        home = data.get("home_team", "").lower()
        away = data.get("away_team", "").lower()
        if (home and home in text_lower) or (away and away in text_lower):
            fetched_at = data.get("fetched_at", "")
            if fetched_at and is_cache_fresh(fetched_at):
                return data
    return None


def format_cache_context(data: dict) -> str:
    """Format cached match data as a readable preamble for Claude."""
    home = data.get("home_team", "Home")
    away = data.get("away_team", "Away")
    fetched_at = data.get("fetched_at", "unknown")
    kickoff = data.get("kickoff_utc", "TBD")

    def fmt(title: str, content) -> str:
        if isinstance(content, dict):
            source = content.get("source", "N/A")
            lines = [f"[CACHED — {title}] (Source: {source}, Last updated: {fetched_at})"]
            for k, v in content.items():
                if k != "source":
                    lines.append(f"  {k}: {v}")
            return "\n".join(lines)
        return f"[CACHED — {title}] {content} (Last updated: {fetched_at})"

    weather = data.get("weather", {})
    weather_line = (
        f"  Temperature: {weather.get('temperature_c')}°C, "
        f"Wind: {weather.get('wind_kmph')} km/h, "
        f"Rain chance: {weather.get('rain_chance_pct')}%, "
        f"Conditions: {weather.get('description')}"
    )

    return "\n".join([
        f"=== CACHED MATCH DATA: {home} vs {away} (Kickoff: {kickoff}) ===",
        f"Pre-fetched at 7am UTC — Last updated: {fetched_at}",
        "",
        f"[CACHED — WEATHER] (Source: {weather.get('source', 'wttr.in')}, Last updated: {fetched_at})",
        weather_line,
        "",
        fmt("LINEUPS", data.get("lineups", {})),
        "",
        fmt("INJURIES", data.get("injuries", {})),
        "",
        fmt("REFEREE", data.get("referee", {})),
        "",
        fmt("HOME DISCIPLINE", data.get("home_discipline", {})),
        "",
        fmt("AWAY DISCIPLINE", data.get("away_discipline", {})),
        "",
        "=== END CACHED DATA — supplement with live search if needed ===",
    ])


# ─── Typing indicator ─────────────────────────────────────────────────────────

async def keep_typing(chat_id: int, bot) -> None:
    """Send a typing action every 4 seconds until cancelled."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    # Inject cached match data if available and fresh
    cached = find_cached_match(user_text)
    if cached:
        enriched = f"{format_cache_context(cached)}\n\nUser question: {user_text}"
        conversation_history[user_id].append({"role": "user", "content": enriched})
        logger.info("Cache hit for user %d: %s vs %s",
                    user_id, cached.get("home_team"), cached.get("away_team"))
    else:
        conversation_history[user_id].append({"role": "user", "content": user_text})

    # Start typing immediately so the user sees activity even while waiting in queue.
    typing_task = asyncio.create_task(
        keep_typing(update.effective_chat.id, context.bot)
    )

    try:
        async with _queue_lock:
            assistant_text = await run_agent_loop(user_id, update, context)

            # Replace enriched entry with the original user text to keep history clean
            conversation_history[user_id][-1] = {"role": "user", "content": user_text}
            conversation_history[user_id].append({"role": "assistant", "content": assistant_text})

            if len(conversation_history[user_id]) > 40:
                conversation_history[user_id] = conversation_history[user_id][-40:]

        await send_reply(update, assistant_text)

    except anthropic.RateLimitError:
        logger.error("Rate limit exhausted after all retries for user %d", user_id)
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
                logger.warning("Rate limited — waiting 60 s before retry %d/2", attempt + 1)
                if attempt == 0:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="⏳ Claude is rate limited right now — retrying in 60 seconds, please hang on…"
                    )
                await asyncio.sleep(60)
        else:
            raise RuntimeError("Exhausted retries")

        logger.info("stop_reason=%s content_types=%s", response.stop_reason,
                    [b.type for b in response.content])

        if response.stop_reason == "tool_use":
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            for block in tool_use_blocks:
                logger.info("Web search query: %s", block.input.get("query", ""))

            loop_messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in tool_use_blocks
            ]
            loop_messages.append({"role": "user", "content": tool_results})
            first_call = False

        else:
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks).strip() or "I couldn't find any information on that."


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    caption = update.message.caption or "Analyse this image and provide detailed betting insights."

    typing_task = asyncio.create_task(
        keep_typing(update.effective_chat.id, context.bot)
    )

    try:
        photo = update.message.photo[-1]
        photo_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")

        if user_id not in conversation_history:
            conversation_history[user_id] = []

        image_message = {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": photo_b64,
                    },
                },
                {
                    "type": "text",
                    "text": caption,
                },
            ],
        }
        conversation_history[user_id].append(image_message)

        async with _queue_lock:
            assistant_text = await run_agent_loop(user_id, update, context)

            # Replace the image entry with a lightweight text placeholder so future
            # conversation turns don't re-send the raw image bytes.
            conversation_history[user_id][-1] = {
                "role": "user",
                "content": f"[Image sent] {caption}",
            }
            conversation_history[user_id].append({"role": "assistant", "content": assistant_text})

            if len(conversation_history[user_id]) > 40:
                conversation_history[user_id] = conversation_history[user_id][-40:]

        await send_reply(update, assistant_text)

    except anthropic.RateLimitError:
        logger.error("Rate limit exhausted after all retries for photo from user %d", user_id)
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
    MAX_LENGTH = 4096
    if len(text) <= MAX_LENGTH:
        await update.message.reply_text(text)
    else:
        for i in range(0, len(text), MAX_LENGTH):
            await update.message.reply_text(text[i:i + MAX_LENGTH])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update %s caused error: %s", update, context.error)


HEALTH_PORT = 8765


async def health_server() -> None:
    """Minimal asyncio HTTP server that responds 200 OK to any GET /health request."""
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


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("leagues", leagues_command))
    app.add_handler(CallbackQueryHandler(league_callback, pattern=r"^league_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)

    app.job_queue.run_repeating(
        score_update_job,
        interval=SCORE_UPDATE_INTERVAL,
        first=60,
    )

    app.job_queue.run_daily(
        morning_cache_job,
        time=dt_time(hour=7, minute=0, tzinfo=timezone.utc),
    )

    logger.info("Bot is starting with web search and league subscriptions enabled...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
