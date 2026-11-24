"""Sozlamalar .env faylidan o'qiladi."""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
DOMAIN = os.getenv("DOMAIN", "localhost")
ACCESS_KEY = os.getenv("ACCESS_KEY", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() != "false"
DB_PATH = os.getenv("DB_PATH", "data/daftar.db")
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
# Docker ichida 0.0.0.0; to'g'ridan-to'g'ri serverda 127.0.0.1 (faqat nginx orqali)
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")

# Bot o'zi savol yuboradigan vaqtlar (HH:MM)
QUIZ_TIMES = _list("QUIZ_TIMES", "09:00,13:00,19:00")
QUIZ_SIZE = int(os.getenv("QUIZ_SIZE", "5"))
# 7 kunlik reja: ertalab vazifalar, kechqurun "bajarildimi?"
PLAN_MORNING = os.getenv("PLAN_MORNING", "08:30")
PLAN_EVENING = os.getenv("PLAN_EVENING", "21:30")

# Ixtiyoriy: to'liq gaplarni AI bilan tekshirish
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
