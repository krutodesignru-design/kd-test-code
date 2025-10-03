import os

# Здесь хранятся токен Telegram-бота и ключ OpenAI.
# Их можно задать через переменные окружения или вписать в кавычках ниже.
# Пример: BOT_TOKEN = os.getenv("BOT_TOKEN", "ваш_telegram_token")
# Настоящие токены не публикуйте в репозитории.

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Название модели OpenAI, по умолчанию используется gpt-5
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
