import asyncio
import os
import tempfile
from io import BytesIO

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

try:
    from docx import Document as DocxDocument
except ImportError:  # pragma: no cover - дополнительная зависимость может отсутствовать
    DocxDocument = None

try:
    import textract
except ImportError:  # pragma: no cover - дополнительная зависимость может отсутствовать
    textract = None

# Состояние диалога
WAITING_TEXT = 1


def build_report_prompt(meeting_text: str) -> str:
    """Формирует запрос к модели для создания отчёта."""
    return (
        "Проанализируй текст встречи. Начни отчёт строкой: '📝 Отчёт по встрече "
        "<имя> - <ссылка>'. Имя и ссылку возьми из текста (если ссылки нет, поставь '-'). "
        "Между блоками вставляй пустую строку. Используй структуру:\n"
        "1. Объект\n2. Состав семьи\n3. Цель клиента\n4. Ожидания\n5. Бюджет клиента\n"
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
    """Анализирует текст встречи и возвращает готовый отчёт."""
    api_key = OPENAI_API_KEY
    if not api_key:
        return "Не удалось провести анализ: отсутствует OPENAI_API_KEY."

    try:
        import openai
    except ImportError:
        return (
            "Не удалось провести анализ: пакет openai не установлен. "
            "Установите его командой: pip install openai"
        )

    try:
        client = openai.OpenAI(api_key=api_key)
        prompt = build_report_prompt(meeting_text)
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content.strip()
    except Exception as exc:  # pragma: no cover - проблемы сети/токена
        return f"Ошибка анализа: {exc}"


async def send_long_message(bot, chat_id: int, text: str) -> None:
    """Отправляет длинное сообщение частями, если оно превышает лимит Telegram."""
    max_len = 4096
    for i in range(0, len(text), max_len):
        await bot.send_message(chat_id=chat_id, text=text[i : i + max_len])


def decode_text_file(data: bytes) -> str:
    """Преобразует содержимое текстового файла в строку с поддержкой UTF-8 и CP1251."""
    for encoding in ("utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


async def extract_document_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Читает текст из присланного файла и возвращает его содержимое."""
    document = update.message.document
    if not document:
        raise RuntimeError("Файл не найден в сообщении.")

    tg_file = await context.bot.get_file(document.file_id)
    file_bytes = await tg_file.download_as_bytearray()
    filename = (document.file_name or "").lower()

    if filename.endswith(".txt"):
        return decode_text_file(bytes(file_bytes)).strip()

    if filename.endswith(".docx"):
        if DocxDocument is None:
            raise RuntimeError(
                "Для обработки DOCX установите пакет python-docx: pip install python-docx"
            )
        doc = DocxDocument(BytesIO(bytes(file_bytes)))
        paragraphs = [p.text for p in doc.paragraphs if p.text]
        return "\n".join(paragraphs).strip()

    if filename.endswith(".doc"):
        if textract is None:
            raise RuntimeError(
                "Для обработки DOC установите пакет textract: pip install textract"
            )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".doc") as tmp:
            tmp.write(bytes(file_bytes))
            tmp_path = tmp.name
        try:
            text_bytes = await asyncio.to_thread(textract.process, tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return decode_text_file(text_bytes).strip()

    raise RuntimeError(
        "Поддерживаются только файлы .txt, .doc и .docx. Пришлите текст встречи в одном из этих форматов."
    )


async def cleanup_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет служебное сообщение с просьбой прислать текст встречи."""
    prompt_id = context.user_data.pop("prompt_id", None)
    if prompt_id:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=prompt_id,
            )
        except Exception:
            pass


async def process_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE, meeting_text: str) -> int:
    """Удаляет сообщения и запускает анализ встречи."""
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass

    await cleanup_prompt(update, context)

    processing_msg = await update.effective_chat.send_message("Встреча обрабатывается...")
    context.user_data["processing_id"] = processing_msg.message_id

    summary = ensure_block_spacing(await analyze_meeting(meeting_text))

    if context.user_data.pop("stopped", False):
        return ConversationHandler.END

    await send_long_message(context.bot, update.effective_chat.id, summary)

    processing_id = context.user_data.pop("processing_id", None)
    if processing_id:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=processing_id,
            )
        except Exception:
            pass
    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /analyze_meeting: удаляет команду и просит текст встречи."""
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass

    prompt_msg = await update.effective_chat.send_message("Пришлите полный текст встречи.")
    context.user_data["prompt_id"] = prompt_msg.message_id
    return WAITING_TEXT


async def received_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает текст встречи, отправленный сообщением."""
    meeting_text = update.message.text or ""
    meeting_text = meeting_text.strip()

    if not meeting_text:
        await update.effective_chat.send_message("Текст встречи пустой. Пришлите содержательное сообщение.")
        return WAITING_TEXT

    return await process_meeting(update, context, meeting_text)


async def received_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает документ с текстом встречи."""
    try:
        meeting_text = await extract_document_text(update, context)
    except RuntimeError as err:
        await update.effective_chat.send_message(str(err))
        return WAITING_TEXT
    except Exception as exc:  # pragma: no cover - неожиданные ошибки при чтении файла
        await update.effective_chat.send_message(f"Не удалось прочитать файл: {exc}")
        return WAITING_TEXT

    if not meeting_text:
        await update.effective_chat.send_message("Файл не содержит текста. Пришлите другой документ или текст сообщением.")
        return WAITING_TEXT

    return await process_meeting(update, context, meeting_text)


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /stop прекращает ожидание текста."""
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        pass

    context.user_data["stopped"] = True

    await cleanup_prompt(update, context)

    processing_id = context.user_data.pop("processing_id", None)
    if processing_id:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=processing_id,
            )
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
                MessageHandler(filters.Document.ALL, received_document),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_text),
            ]
        },
        fallbacks=[CommandHandler("stop", stop)],
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("stop", stop))

    application.run_polling()  # Запуск бота и ожидание новых сообщений


if __name__ == "__main__":
    main()
