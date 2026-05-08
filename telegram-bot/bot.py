import os
import base64
import logging
from datetime import datetime, timezone
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

SYSTEM_PROMPT = (
    "You are a knowledgeable football (soccer) assistant with real-time web search capability. "
    "You specialise in providing up-to-date football news, live scores, match results, "
    "injury updates, team news, transfer rumours, fixtures, standings, and player statistics. "
    "You cover all major leagues and competitions worldwide (Premier League, La Liga, "
    "Serie A, Bundesliga, Ligue 1, Champions League, World Cup, etc.). "
    "When asked about recent events, match results, injuries, or any current football news, "
    "always use your web search tool to retrieve the latest information before responding. "
    "Present information in a clear, structured way. If scores or news are unavailable, say so honestly.\n\n"
    "MATCH ANALYSIS RULE: Whenever you analyse a match (preview, review, or tactical breakdown), "
    "you MUST always include ALL of the following sections, each sourced via web search:\n\n"
    "DISCIPLINE STATS (per team):\n"
    "1. Average yellow cards per game this season.\n"
    "2. Total yellow cards across their last 5 matches.\n\n"
    "REFEREE INFO:\n"
    "3. The appointed referee's full name.\n"
    "4. The referee's average yellow cards per game this season.\n"
    "5. The referee's red card count this season.\n"
    "6. The referee's penalty decisions record this season (penalties awarded per game or total).\n\n"
    "WEATHER FORECAST:\n"
    "7. Real-time weather forecast for the match city and stadium on match day, including: "
    "temperature (°C), wind speed (km/h), and rain probability (%).\n\n"
    "Present each section clearly with a heading. "
    "If any data point is unavailable after searching, state that explicitly rather than omitting the section.\n\n"
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
        response = client.messages.create(
            model="claude-sonnet-4-6",
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({"role": "user", "content": user_text})

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        assistant_text = await run_agent_loop(user_id, update, context)

        conversation_history[user_id].append({"role": "assistant", "content": assistant_text})

        if len(conversation_history[user_id]) > 40:
            conversation_history[user_id] = conversation_history[user_id][-40:]

        await send_reply(update, assistant_text)

    except anthropic.APIError as e:
        logger.error("Anthropic API error: %s", e)
        await update.message.reply_text(
            "Sorry, I encountered an error communicating with Claude. Please try again."
        )
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        await update.message.reply_text("An unexpected error occurred. Please try again.")


async def run_agent_loop(
    user_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str:
    loop_messages = list(conversation_history[user_id])
    search_performed = False

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL],
            messages=loop_messages,
        )

        logger.info("stop_reason=%s content_types=%s", response.stop_reason,
                    [b.type for b in response.content])

        if response.stop_reason == "tool_use":
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not search_performed:
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action="typing"
                )
                search_performed = True

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

        else:
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks).strip() or "I couldn't find any information on that."


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    caption = update.message.caption or "Analyse this image and provide detailed betting insights."

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

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

    except anthropic.APIError as e:
        logger.error("Anthropic API error processing photo: %s", e)
        await update.message.reply_text(
            "Sorry, I encountered an error analysing that image. Please try again."
        )
    except Exception as e:
        logger.error("Unexpected error processing photo: %s", e)
        await update.message.reply_text("Sorry, I couldn't process that image. Please try again.")


async def send_reply(update: Update, text: str) -> None:
    MAX_LENGTH = 4096
    if len(text) <= MAX_LENGTH:
        await update.message.reply_text(text)
    else:
        for i in range(0, len(text), MAX_LENGTH):
            await update.message.reply_text(text[i:i + MAX_LENGTH])


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update %s caused error: %s", update, context.error)


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

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

    logger.info("Bot is starting with web search and league subscriptions enabled...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
