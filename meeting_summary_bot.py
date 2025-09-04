import asyncio
import calendar
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import feedparser
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

# Ссылки на RSS-ленты для ежедневных новостей
NEWS_FEEDS = [
    "https://feeds.archdaily.com/archdaily",
    "https://www.dezeen.com/feed/",
    "https://www.designboom.com/feed/",
    "https://www.architecturaldigest.com/feed/latest",
    "https://www.theguardian.com/artanddesign/architecture/rss",
    "https://www.wallpaper.com/rss",
    "https://www.elledecor.com/rss/all.xml",
    "https://www.architecturalrecord.com/rss/articles",
    "https://www.interiordesign.net/rss",
]


def build_report_prompt(meeting_text: str) -> str:
    """Формирует запрос к модели для создания отчёта."""
    return (
        "Проанализируй текст встречи. Начни отчёт строкой: 'Отчёт о встрече "
        "<имя> - <ссылка>'. Имя и ссылку возьми из текста (если ссылки нет, поставь '-')."
        " Между блоками вставляй пустую строку. Используй структуру:\n"
        "1. Объект\n2. Состав семьи\n3. Цель клиента\n4. Ожидания\n5. Бюджет\n"
        "6. Стоимость и тарифы (озвученные вами)\n7. Сроки\n8. Дополнительные моменты\n"
        "🔎 Боли клиента\n✅ Где дожал\n⚠️ Где не дожал\n\n"
        f"Текст встречи:\n{meeting_text}"
    )


def ensure_block_spacing(text: str) -> str:
    """Добавляет пустую строку перед новым блоком отчёта."""
    lines = text.splitlines()
    result = []
    for i, line in enumerate(lines):
        result.append(line)
        if i < len(lines) - 1:
            nxt = lines[i + 1]
            if nxt and (nxt[0].isdigit() or nxt.startswith(("🔎", "✅", "⚠️"))):
                result.append("")
    return "\n".join(result).strip()


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
        # обращаемся к OpenAI в отдельном потоке, чтобы не блокировать обработку других сообщений
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=OPENAI_MODEL,  # название модели задаётся в config.py
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:  # pragma: no cover - проблемы сети/токена
        return f"Ошибка анализа: {exc}"


def _parse_date(entry) -> datetime | None:
    """Преобразует дату публикации RSS-элемента в datetime."""
    if not entry.get("published_parsed"):
        return None
    dt = datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo("Europe/Moscow"))


def collect_news() -> list[dict]:
    """Собирает статьи из RSS-лент и выбирает последние новости."""
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    items: list[dict] = []
    for url in NEWS_FEEDS:
        feed = feedparser.parse(url)
        source = feed.feed.get("title", url)
        for entry in feed.entries:
            dt = _parse_date(entry)
            if not dt:
                continue
            if now - dt > timedelta(hours=72):
                continue
            items.append(
                {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                    "source": source,
                    "published": dt,
                }
            )
    items.sort(key=lambda x: x["published"], reverse=True)
    # уникальные по заголовку
    seen = set()
    unique = []
    for item in items:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        unique.append(item)
    fresh = [i for i in unique if now - i["published"] <= timedelta(hours=24)]
    if len(fresh) < 3:
        fresh = unique[:3]
    return fresh[:3]


async def format_news(items: list[dict]) -> str:
    """Формирует текст с новостями, используя OpenAI при наличии ключа."""
    if not items:
        return "Свежих новостей не найдено."
    if not OPENAI_API_KEY:
        parts = []
        for it in items:
            parts.append(
                "📰 {title} — {src}, {date}.\n{summary}\nСсылка: {link}\nИдея для KD: -".format(
                    title=it["title"],
                    src=it["source"],
                    date=it["published"].strftime("%d.%m.%Y"),
                    summary=it["summary"].strip(),
                    link=it["link"],
                )
            )
        return "\n\n".join(parts)
    try:
        import openai

        client = openai.OpenAI(api_key=OPENAI_API_KEY)
        articles = []
        for it in items:
            articles.append(
                f"Заголовок: {it['title']}\nИсточник: {it['source']}\n"
                f"Дата: {it['published'].strftime('%d.%m.%Y')}\n"
                f"Ссылка: {it['link']}\nОписание: {it['summary']}"
            )
        prompt = (
            "На основе следующих материалов сформируй три новости по шаблону:"
            "\n📰 {Заголовок} — {Источник}, {Дата}.\n"
            "{Короткое резюме}.\nСсылка: {URL}\nИдея для KD: {мысль}.\n\n"
            "Материалы:\n" + "\n\n".join(articles)
        )
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:  # pragma: no cover
        return f"Не удалось сформировать новости: {exc}"


async def send_daily_news(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет ежедневную подборку новостей."""
    items = collect_news()
    text = await format_news(items)
    await context.bot.send_message(chat_id=context.job.chat_id, text=text)


async def news_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /new_today: присылает свежие новости немедленно."""
    # удаляем сообщение пользователя с командой
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass

    # собираем новости и отправляем их в чат
    items = collect_news()
    text = await format_news(items)
    await update.effective_chat.send_message(text=text)

    # регистрируем ежедневную рассылку, если ещё не сделано
    jobs = context.application.bot_data.setdefault("news_jobs", set())
    chat_id = update.effective_chat.id
    if chat_id not in jobs:
        context.application.job_queue.run_daily(
            send_daily_news,
            time=time(hour=10, minute=0, tzinfo=ZoneInfo("Europe/Moscow")),
            chat_id=chat_id,
            name=f"daily_news_{chat_id}",
        )
        jobs.add(chat_id)


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

    # регистрируем ежедневную отправку новостей для этого чата
    jobs = context.application.bot_data.setdefault("news_jobs", set())
    chat_id = update.effective_chat.id
    if chat_id not in jobs:
        context.application.job_queue.run_daily(
            send_daily_news,
            time=time(hour=10, minute=0, tzinfo=ZoneInfo("Europe/Moscow")),
            chat_id=chat_id,
            name=f"daily_news_{chat_id}",
        )
        jobs.add(chat_id)
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
    context.user_data["processing_id"] = processing_msg.message_id

    summary = await analyze_meeting(meeting_text)
    summary = ensure_block_spacing(summary)

    # если пользователь успел отправить /stop, не присылаем отчёт
    if context.user_data.pop("stopped", False):
        return ConversationHandler.END

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=summary  # отправляем отчёт без Markdown, чтобы избежать ошибок
    )

    # удаляем сообщение об обработке после отправки отчёта
    processing_id = context.user_data.pop("processing_id", None)
    if processing_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing_id)
        except Exception:
            pass
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

    # помечаем, что анализ нужно прекратить
    context.user_data["stopped"] = True

    # удаляем подсказку, если она ещё висит
    prompt_id = context.user_data.pop("prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=prompt_id)
        except Exception:
            pass

    # удаляем сообщение об обработке, если оно есть
    processing_id = context.user_data.pop("processing_id", None)
    if processing_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing_id)
        except Exception:
            pass

    await update.effective_chat.send_message("Анализ остановлен, жду нового запроса")
    return ConversationHandler.END


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    application = ApplicationBuilder().token(BOT_TOKEN).concurrent_updates(True).build()

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
    # обработчик /stop вне диалога
    application.add_handler(CommandHandler("stop", stop))
    # команда для немедленного запроса новостей
    application.add_handler(CommandHandler("new_today", news_today))

    application.run_polling()  # Запуск бота и ожидание новых сообщений


if __name__ == "__main__":
    main()
