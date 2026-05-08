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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hello! I'm a Claude-powered assistant. Send me any message and I'll respond using Anthropic's Claude AI.\n\n"
        "Use /clear to reset our conversation history."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conversation_history.pop(user_id, None)
    await update.message.reply_text("Conversation history cleared. Starting fresh!")


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
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=(
                "You are a helpful, friendly, and knowledgeable AI assistant. "
                "Provide clear, accurate, and concise responses."
            ),
            messages=conversation_history[user_id],
        )

        assistant_text = response.content[0].text

        conversation_history[user_id].append({
            "role": "assistant",
            "content": assistant_text
        })

        if len(conversation_history[user_id]) > 40:
            conversation_history[user_id] = conversation_history[user_id][-40:]

        MAX_LENGTH = 4096
        if len(assistant_text) <= MAX_LENGTH:
            await update.message.reply_text(assistant_text)
        else:
            for i in range(0, len(assistant_text), MAX_LENGTH):
                await update.message.reply_text(assistant_text[i:i + MAX_LENGTH])

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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update %s caused error: %s", update, context.error)


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
