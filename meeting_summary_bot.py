import os
import asyncio

from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Conversation states
WAITING_TEXT = 1


def build_report_prompt(meeting_text: str) -> str:
    """Create a prompt for the language model that produces the summary."""
    return (
        "Сформируй структурированный отчёт по встрече и выдели пункты, где пользователь"
        " дожал и не дожал. Используй следующую структуру:\n"
        "1. Объект\n2. Состав семьи\n3. Цель клиента\n4. Ожидания\n5. Бюджет\n"
        "6. Стоимость и тарифы (озвученные вами)\n7. Сроки\n8. Дополнительные моменты\n"
        "🔎 Боли клиента\n✅ Где дожал\n⚠️ Где не дожал\n\n"
        f"Текст встречи:\n{meeting_text}"
    )


async def analyze_meeting(meeting_text: str) -> str:
    """Analyse meeting text and return a formatted summary.

    The function tries to use OpenAI's ChatGPT model if an ``OPENAI_API_KEY`` is
    available. Otherwise, it falls back to returning a placeholder message.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "Не удалось провести анализ: отсутствует OPENAI_API_KEY."

    try:
        import openai

        client = openai.OpenAI(api_key=api_key)
        prompt = build_report_prompt(meeting_text)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return completion.choices[0].message["content"].strip()
    except Exception as exc:  # pragma: no cover - network/credentials issues
        return f"Ошибка анализа: {exc}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: ask user for meeting text."""
    await update.message.reply_text("Пришлите полный текст встречи.")
    return WAITING_TEXT


async def received_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the meeting text, delete it, and return the analysis."""
    meeting_text = update.message.text
    # delete user's message to keep group chat clean
    try:
        await update.message.delete()
    except Exception:
        pass  # ignore if bot has no rights

    summary = await analyze_meeting(meeting_text)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=summary,
        parse_mode=constants.ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def main() -> None:
    token = os.environ["BOT_TOKEN"]
    application = ApplicationBuilder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("analyze_meeting", start)],
        states={WAITING_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await application.updater.idle()


if __name__ == "__main__":
    asyncio.run(main())
