import os

# Tokens and keys are centralized here for easy editing.
# Either edit the strings below or set the corresponding
# environment variables `BOT_TOKEN` and `OPENAI_API_KEY`.

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY")
