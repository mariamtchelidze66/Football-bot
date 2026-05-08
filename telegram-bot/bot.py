import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
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

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}

SYSTEM_PROMPT = (
    "You are a knowledgeable football (soccer) assistant with real-time web search capability. "
    "You specialise in providing up-to-date football news, live scores, match results, "
    "injury updates, team news, transfer rumours, fixtures, standings, and player statistics. "
    "You cover all major leagues and competitions worldwide (Premier League, La Liga, "
    "Serie A, Bundesliga, Ligue 1, Champions League, World Cup, etc.). "
    "When asked about recent events, match results, injuries, or any current football news, "
    "always use your web search tool to retrieve the latest information before responding. "
    "Present information in a clear, structured way. If scores or news are unavailable, say so honestly."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚽ Hello! I'm a football assistant powered by Claude with live web search.\n\n"
        "I can help you with:\n"
        "• Latest match results & live scores\n"
        "• Team news & injury updates\n"
        "• Transfer rumours & signings\n"
        "• Fixtures & standings\n"
        "• Player stats & analysis\n\n"
        "Just ask me anything about football!\n\n"
        "Use /clear to reset our conversation."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conversation_history.pop(user_id, None)
    await update.message.reply_text("Conversation cleared. Starting fresh!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({
        "role": "user",
        "content": user_text
    })

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        assistant_text = await run_agent_loop(user_id, update, context)

        conversation_history[user_id].append({
            "role": "assistant",
            "content": assistant_text
        })

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
        await update.message.reply_text(
            "An unexpected error occurred. Please try again."
        )


async def run_agent_loop(
    user_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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
                    chat_id=update.effective_chat.id,
                    action="typing"
                )
                search_performed = True

            for block in tool_use_blocks:
                logger.info("Web search query: %s", block.input.get("query", ""))

            loop_messages.append({
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            })

            tool_results = []
            for block in tool_use_blocks:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "",
                })

            loop_messages.append({
                "role": "user",
                "content": tool_results,
            })

        else:
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks).strip() or "I couldn't find any information on that."


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot is starting with web search enabled...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
