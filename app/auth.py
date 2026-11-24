"""Kirish: Telegram Mini App imzosi (initData) va brauzer uchun imzolangan havola."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from . import config

# Mini App ochiq turgan bo'lsa ham initData o'zgarmaydi; shuncha vaqtgacha qabul qilamiz.
INIT_DATA_MAX_AGE = 7 * 24 * 3600


def _secret() -> bytes:
    return config.ACCESS_KEY.encode()


def parse_init_data(init_data: str) -> dict | None:
    """Telegram.WebApp.initData ni tekshiradi va foydalanuvchini qaytaradi (id, first_name, …).

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data or not config.BOT_TOKEN:
        return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = data.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    want = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not got or not hmac.compare_digest(want, got):
        return None
    try:
        if time.time() - int(data.get("auth_date", "0")) > INIT_DATA_MAX_AGE:
            return None
        user = json.loads(data.get("user") or "{}")
    except ValueError:
        return None
    return user if isinstance(user, dict) and isinstance(user.get("id"), int) else None


def _sig(uid: int) -> str:
    return hmac.new(_secret(), f"login:{uid}".encode(), hashlib.sha256).hexdigest()[:32]


def token_for(uid: int) -> str:
    return f"{uid}.{_sig(uid)}"


def uid_from_token(token: str) -> int | None:
    uid, _, sig = (token or "").partition(".")
    if not uid.isdigit() or not config.ACCESS_KEY:
        return None
    return int(uid) if hmac.compare_digest(_sig(int(uid)), sig) else None


def login_url(uid: int) -> str:
    """Telegram'dan tashqarida (brauzerda) ochish uchun shaxsiy havola."""
    return f"https://{config.DOMAIN}/login?t={token_for(uid)}"


def app_url() -> str:
    return f"https://{config.DOMAIN}/"
