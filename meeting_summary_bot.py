from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, OPENAI_API_KEY, OPENAI_MODEL

# Состояния диалога
WAITING_TEXT = 1


def build_report_prompt(meeting_text: str) -> str:
    """Формирует запрос к модели для создания отчёта."""
    return (
        "Сформируй структурированный отчёт по встрече и выдели пункты, где пользователь"
        " дожал и не дожал. Используй следующую структуру:\n"
        "1. Объект\n2. Состав семьи\n3. Цель клиента\n4. Ожидания\n5. Бюджет\n"
        "6. Стоимость и тарифы (озвученные вами)\n7. Сроки\n8. Дополнительные моменты\n"
        "🔎 Боли клиента\n✅ Где дожал\n⚠️ Где не дожал\n\n"
        f"Текст встречи:\n{meeting_text}"
    )


async def analyze_meeting(meeting_text: str) -> str:
    """Анализирует текст встречи и возвращает готовый отчёт.

    При наличии ``OPENAI_API_KEY`` используется модель OpenAI, иначе
    возвращается сообщение об ошибке.
    """
    api_key = OPENAI_API_KEY
    if not api_key:
        return "Не удалось провести анализ: отсутствует OPENAI_API_KEY."

    try:
        import openai

        client = openai.OpenAI(api_key=api_key)
        prompt = build_report_prompt(meeting_text)
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,  # название модели задаётся в config.py
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:  # pragma: no cover - проблемы сети/токена
        return f"Ошибка анализа: {exc}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /analyze_meeting: просит прислать текст встречи."""
    await update.message.reply_text("Пришлите полный текст встречи.")
    return WAITING_TEXT


async def received_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает текст встречи: удаляет его и присылает анализ."""
    meeting_text = update.message.text
    # удаляем сообщение пользователя
    try:
        await update.message.delete()
    except Exception:
        pass  # игнорируем, если нет прав

    summary = await analyze_meeting(meeting_text)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=summary,
        parse_mode=constants.ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена диалога."""
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("analyze_meeting", start)],
        states={WAITING_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    application.run_polling()  # Запуск бота и ожидание новых сообщений


if __name__ == "__main__":
    main()
