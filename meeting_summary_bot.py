from telegram import Update
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
            # модель не поддерживает параметр temperature, используем значение по умолчанию
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:  # pragma: no cover - проблемы сети/токена
        return f"Ошибка анализа: {exc}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /analyze_meeting: удаляет команду и просит текст встречи."""
    # удаляем сообщение с командой пользователя
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass  # игнорируем, если нет прав или сообщение уже удалено

    # отправляем подсказку и сохраняем её id для последующего удаления
    prompt_msg = await update.effective_chat.send_message("Пришлите полный текст встречи.")
    context.user_data["prompt_id"] = prompt_msg.message_id
    return WAITING_TEXT


async def received_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает текст встречи: удаляет сообщения и присылает анализ."""
    meeting_text = update.message.text

    # удаляем сообщение пользователя с текстом встречи
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass  # игнорируем, если нет прав или сообщение уже удалено

    # удаляем ранее отправленную подсказку "Пришлите полный текст встречи"
    prompt_id = context.user_data.pop("prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prompt_id)
        except Exception:
            pass

    # отправляем сообщение о том, что идёт обработка
    processing_msg = await update.effective_chat.send_message("Встреча обрабатывается...")

    summary = await analyze_meeting(meeting_text)

    # удаляем сообщение об обработке, если оно ещё существует
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id, message_id=processing_msg.message_id
        )
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=summary
        # отправляем отчёт без форматирования Markdown, чтобы избежать ошибок разметки
    )
    return ConversationHandler.END


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /stop прекращает ожидание текста."""
    # удаляем сообщение пользователя с командой /stop
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id, message_id=update.message.message_id
        )
    except Exception:
        pass

    # удаляем подсказку, если она ещё висит
    prompt_id = context.user_data.pop("prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prompt_id)
        except Exception:
            pass

    await update.effective_chat.send_message("Анализ остановлен, жду нового запроса")
    return ConversationHandler.END


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("analyze_meeting", start)],
        states={
            WAITING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_text)
            ]
        },
        fallbacks=[CommandHandler("stop", stop)],
    )
    application.add_handler(conv_handler)

    application.run_polling()  # Запуск бота и ожидание новых сообщений


if __name__ == "__main__":
    main()
