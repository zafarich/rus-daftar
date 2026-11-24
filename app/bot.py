"""Telegram bot: har bir foydalanuvchiga savol beradi, javobni tekshiradi, 7 kunlik rejani so'rab turadi."""
import asyncio
import logging
from datetime import datetime
from html import escape as _escape

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.filters import Command, CommandObject
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message, WebAppInfo)

from . import auth, checker, config, db

log = logging.getLogger(__name__)

# Bir vaqtda ko'p odamga yuborishda Telegram limitidan (≈30 xabar/soniya) oshmaslik uchun
BROADCAST_PAUSE = 0.05


def esc(s) -> str:
    return _escape(str(s), quote=False)


class UserMiddleware(BaseMiddleware):
    """Har bir xabarda foydalanuvchini bazaga yozadi va handler'ga uid beradi."""

    async def __call__(self, handler, event, data):
        u = data.get("event_from_user")
        if not u or u.is_bot:
            return None
        db.ensure_user(u.id, u.first_name, u.username)
        data["uid"] = u.id
        return await handler(event, data)


router = Router()
router.message.filter(F.chat.type == "private")   # guruhlarda jim turadi
router.message.middleware(UserMiddleware())
router.callback_query.middleware(UserMiddleware())

KIND_LABEL = {
    "card": "🔤 Ruschasini ayting",
    "translate": "🔤 Ruschaga tarjima qiling",
    "sentence": "✍️ To'liq gap yozing",
    "gap": "🧩 Bo'sh joyni to'ldiring",
    "ending": "🔚 Qo'shimchani yozing",
    "choice": "☑️ To'g'ri variantni tanlang",
    "drill": "⚡️ Tez ayting (2 soniyada!)",
}

COMMANDS = [
    BotCommand(command="quiz", description="Savollarni boshlash (/quiz 10)"),
    BotCommand(command="daftar", description="Daftarni ochish"),
    BotCommand(command="plan", description="7 kunlik reja va bugungi vazifa"),
    BotCommand(command="hint", description="Joriy savolga yordam"),
    BotCommand(command="skip", description="Savolni o'tkazish"),
    BotCommand(command="stop", description="Savollarni to'xtatish"),
    BotCommand(command="topics", description="Mavzular ro'yxati"),
    BotCommand(command="stats", description="Natijalar"),
    BotCommand(command="off", description="Avtomatik xabarlarni o'chirish"),
    BotCommand(command="on", description="Avtomatik xabarlarni yoqish"),
]


def web_kb(uid: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📓 Daftarni ochish", web_app=WebAppInfo(url=auth.app_url()))],
        [InlineKeyboardButton(text="🌐 Brauzerda ochish", url=auth.login_url(uid))],
    ])


def plan_kb(tid, day):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Bajarildi", callback_data=f"pl:{tid}:{day}:done"),
        InlineKeyboardButton(text="❌ Bajarilmadi", callback_data=f"pl:{tid}:{day}:missed"),
    ]])


def next_quiz_time() -> str | None:
    hm = db.now().strftime("%H:%M")
    later = sorted(t for t in config.QUIZ_TIMES if t > hm)
    return later[0] if later else (sorted(config.QUIZ_TIMES)[0] + " (ertaga)" if config.QUIZ_TIMES else None)


async def send(bot: Bot, uid: int, text: str, **kw):
    """Xabar yuboradi; bot bloklangan bo'lsa foydalanuvchini belgilaydi, limitda kutadi."""
    for attempt in range(2):
        try:
            return await bot.send_message(uid, text, **kw)
        except TelegramRetryAfter as e:
            if attempt:
                raise
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            db.set_blocked(uid)
            log.info("%s botni bloklagan, avtomatik xabarlar to'xtatildi", uid)
            return None
        except TelegramBadRequest as e:
            if "chat not found" in str(e).lower():
                db.set_blocked(uid)
                return None
            raise


def quiz_key(uid: int) -> str:
    return f"quiz:{uid}"


# ---------- savol-javob sessiyasi ----------
def q_text(it, num, total):
    lines = [f"<i>Savol {num}/{total}</i>", f"{KIND_LABEL.get(it['kind'], '❓')}:",
             f"<b>{esc(it['prompt'])}</b>"]
    if it["kind"] == "drill":
        ex = it["extra"]
        lines.append(f"({esc(ex.get('uz') or '')} → {esc(ex.get('base') or '')})")
    if it["kind"] != "choice":
        lines.append("<i>/hint yordam · /skip o'tkazish</i>")
    return "\n".join(lines)


def choice_kb(it):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=o, callback_data=f"ch:{it['id']}:{i}")
        for i, o in enumerate(it["options"])]])


def fb_text(res, it, answer):
    if res["correct"]:
        t = "✅ <b>To'g'ri!</b>"
        if checker.norm(res["full"]) != checker.norm(answer):
            t += f"\n{esc(res['full'])}"
    else:
        t = (f"❌ Javobingiz: <s>{esc(answer) or '—'}</s>\n"
             f"To'g'ri: <b>{esc(res['full'])}</b>")
        if it.get("note"):
            t += f"\n📌 {esc(it['note'])}"
    if res.get("comment"):
        t += f"\n💬 {esc(res['comment'])}"
    return t


async def start_quiz(bot: Bot, uid: int, n: int | None = None):
    n = max(1, min(int(n or config.QUIZ_SIZE), 30))
    st = {"left": n, "total": n, "ok": 0, "item": None, "asked": [], "started": db.iso(db.now())}
    db.kv_set(quiz_key(uid), st)
    await ask_next(bot, uid, st)


async def ask_next(bot: Bot, uid: int, st):
    if st["left"] <= 0:
        db.kv_del(quiz_key(uid))
        nxt = next_quiz_time()
        await send(bot, uid, f"🏁 Tugadi: <b>{st['ok']}/{st['total']}</b>.\n"
                             f"Keyingi savollar: {nxt or '—'}. Hozir yana: /quiz")
        return
    it = db.next_item(uid, exclude=st["asked"], prefer_other=len(st["asked"]) % 5 == 4)
    if not it:
        db.kv_del(quiz_key(uid))
        await send(bot, uid, "Hozircha savol yo'q. Mavzular: /topics")
        return
    st["item"] = it["id"]
    st["asked"].append(it["id"])
    db.kv_set(quiz_key(uid), st)
    num = st["total"] - st["left"] + 1
    await send(bot, uid, q_text(it, num, st["total"]),
               reply_markup=choice_kb(it) if it["kind"] == "choice" else None)


async def handle_answer(bot: Bot, uid: int, answer: str):
    st = db.kv_get(quiz_key(uid))
    if not st or not st.get("item"):
        return False
    it = db.get_item(st["item"])
    if not it:
        db.kv_del(quiz_key(uid))
        return False
    res = await checker.check(it, answer)
    db.review(uid, it["id"], res["correct"], "telegram", answer)
    st["ok"] += int(res["correct"])
    st["left"] -= 1
    st["item"] = None
    db.kv_set(quiz_key(uid), st)
    await send(bot, uid, fb_text(res, it, answer))
    await ask_next(bot, uid, st)
    return True


# ---------- buyruqlar ----------
@router.message(Command("myid"))
async def cmd_myid(m: Message):
    await m.answer(f"Telegram ID: <code>{m.from_user.id}</code>")


@router.message(Command("start", "help"))
async def cmd_start(m: Message, uid: int):
    t = db.active_topic(uid)
    await m.answer(
        "Salom! Men rus tili daftaringizman.\n\n"
        f"Har kuni {', '.join(config.QUIZ_TIMES)} da o'zim savol yuboraman: javobni shu yerga yozing.\n"
        f"Ertalab {config.PLAN_MORNING} da bugungi vazifani, kechqurun {config.PLAN_EVENING} da "
        "«bajarildimi?» deb so'rayman.\n\n"
        f"Faol mavzu: <b>{esc(t['title']) if t else 'yo‘q'}</b>\n\n"
        "/quiz — hozir boshlash\n/plan — 7 kunlik reja\n/topics — mavzular\n/stats — natijalar\n"
        "/off — avtomatik xabarlarni o'chirish",
        reply_markup=web_kb(uid))


@router.message(Command("daftar"))
async def cmd_daftar(m: Message, uid: int):
    await m.answer("Daftar Telegram ichida ochiladi. Ovoz bilan javob berish va eshitish "
                   "Telegram ichida ishlamasa, «Brauzerda ochish» tugmasini bosing.",
                   reply_markup=web_kb(uid))


@router.message(Command("quiz"))
async def cmd_quiz(m: Message, command: CommandObject, bot: Bot, uid: int):
    n = int(command.args) if command.args and command.args.strip().isdigit() else None
    await start_quiz(bot, uid, n)


@router.message(Command("skip"))
async def cmd_skip(m: Message, bot: Bot, uid: int):
    if not await handle_answer(bot, uid, ""):
        await m.answer("Hozir savol yo'q. /quiz")


@router.message(Command("stop"))
async def cmd_stop(m: Message, uid: int):
    db.kv_del(quiz_key(uid))
    await m.answer(f"To'xtatildi. Keyingi savollar: {next_quiz_time() or '—'}")


@router.message(Command("off"))
async def cmd_off(m: Message, uid: int):
    db.set_auto(uid, False)
    db.kv_del(quiz_key(uid))
    await m.answer("Avtomatik savol va eslatmalar o'chirildi. O'zingiz xohlaganda: /quiz\nQayta yoqish: /on")


@router.message(Command("on"))
async def cmd_on(m: Message, uid: int):
    db.set_auto(uid, True)
    await m.answer(f"Avtomatik xabarlar yoqildi. Keyingi savollar: {next_quiz_time() or '—'}")


@router.message(Command("hint"))
async def cmd_hint(m: Message, uid: int):
    st = db.kv_get(quiz_key(uid))
    it = db.get_item(st["item"]) if st and st.get("item") else None
    if not it:
        await m.answer("Hozir savol yo'q.")
        return
    tips = [x for x in (it.get("hint"), it.get("note")) if x]
    a = it["answers"][0]
    tips.append(f"Javob {len(a)} belgidan iborat, «{a[0]}…» bilan boshlanadi.")
    await m.answer("💡 " + "\n".join(esc(x) for x in tips))


@router.message(Command("topics"))
async def cmd_topics(m: Message, uid: int):
    ts = db.list_topics(uid)
    if not ts:
        await m.answer("Hozircha mavzu yo'q.")
        return
    lines = [f"{'⭐️' if t['active'] else '▫️'} <b>{esc(t['title'])}</b>\n"
             f"   <code>/topic {t['slug']}</code> · {t['learned'] or 0}/{t['n_items']} o'zlashtirildi"
             for t in ts]
    await m.answer("\n".join(lines))


@router.message(Command("topic"))
async def cmd_topic(m: Message, command: CommandObject, uid: int):
    slug = (command.args or "").strip()
    try:
        t = db.activate_topic(uid, slug)
    except db.TopicError as e:
        await m.answer(esc(str(e)))
        return
    await m.answer(f"⭐️ Faol mavzu: <b>{esc(t['title'])}</b>. Reja: /plan")


@router.message(Command("restart"))
async def cmd_restart(m: Message, uid: int):
    t = db.active_topic(uid)
    if not t:
        await m.answer("Faol mavzu yo'q.")
        return
    db.activate_topic(uid, t["slug"], restart=True)
    await m.answer("7 kunlik reja bugundan qaytadan boshlandi. /plan")


@router.message(Command("plan"))
async def cmd_plan(m: Message, uid: int):
    t = db.active_topic(uid)
    if not t:
        await m.answer("Faol mavzu yo'q. /topics")
        return
    plan, log_, day = db.plan_of(t), db.plan_log(uid, t["id"]), db.plan_day(t)
    mark = {"done": "✅", "missed": "❌"}
    lines = [f"<b>{esc(t['title'])}</b>"]
    for i, d in enumerate(plan, 1):
        s = mark.get(log_.get(i), "▫️")
        lines.append(f"{s} {i}-kun{' ← bugun' if i == day else ''}")
    kb = None
    if day and 1 <= day <= len(plan):
        lines.append(f"\n<b>Bugungi vazifalar ({day}-kun):</b>")
        lines += [f"• {esc(x)}" for x in plan[day - 1]["tasks"]]
        if day not in log_:
            kb = plan_kb(t["id"], day)
    elif not day:
        lines.append("\nReja hali boshlanmagan: /restart")
    await m.answer("\n".join(lines), reply_markup=kb)


@router.message(Command("stats"))
async def cmd_stats(m: Message, uid: int):
    s = db.stats(uid)

    def pct(r):
        return f"{r['ok']}/{r['n']} ({round(100 * r['ok'] / r['n']) if r['n'] else 0}%)"

    lines = [f"Bugun: <b>{pct(s['today'])}</b>", f"7 kun: <b>{pct(s['week'])}</b>", ""]
    lines += [f"{'⭐️' if t['active'] else '▫️'} {esc(t['title'])}: {t['learned'] or 0}/{t['n_items']}"
              for t in s["topics"]]
    lines.append("\n<i>O'zlashtirildi = kamida 3 marta ketma-ket to'g'ri.</i>")
    await m.answer("\n".join(lines))


@router.callback_query(F.data.startswith("ch:"))
async def cb_choice(cb: CallbackQuery, bot: Bot, uid: int):
    _, item_id, idx = cb.data.split(":")
    st = db.kv_get(quiz_key(uid))
    if not st or st.get("item") != int(item_id):
        await cb.answer("Bu savol yopilgan")
        return
    it = db.get_item(int(item_id))
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await handle_answer(bot, uid, it["options"][int(idx)])


@router.callback_query(F.data.startswith("pl:"))
async def cb_plan(cb: CallbackQuery, bot: Bot, uid: int):
    _, tid, day, status = cb.data.split(":")
    tid, day = int(tid), int(day)
    db.set_plan(uid, tid, day, status)
    await cb.answer("Belgilandi")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"{day}-kun: {'✅ bajarildi' if status == 'done' else '❌ bajarilmadi'}"
                            + ("" if status == "done" else "\nErtaga shu vazifalarni ham qo'shib qiling."))
    t = db.q1("SELECT * FROM topics WHERE id=?", (tid,))
    if t and day >= len(db.plan_of(t)):
        await send_plan_summary(bot, uid, t)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message, bot: Bot, uid: int):
    if not await handle_answer(bot, uid, m.text):
        await m.answer(f"Hozir savol yo'q. Boshlash: /quiz\nKeyingi avtomatik savollar: {next_quiz_time() or '—'}")


# ---------- rejalashtiruvchi ----------
async def send_plan_summary(bot: Bot, uid: int, t):
    flag = f"plan_done:{uid}:{t['id']}"
    if db.kv_get(flag):
        return
    db.kv_set(flag, True)
    plan, log_ = db.plan_of(t), db.plan_log(uid, t["id"])
    done = sum(1 for v in log_.values() if v == "done")
    await send(bot, uid,
               f"🏁 <b>{esc(t['title'])}</b>: {len(plan)} kundan <b>{done}</b> tasi bajarildi.\n"
               + ("Zo'r! Keyingi mavzu: /topics" if done >= len(plan) - 1
                  else "Rejani qaytadan boshlash: /restart"))


async def quiz_job(bot: Bot, uid: int):
    st = db.kv_get(quiz_key(uid))
    stale = st and (db.now() - datetime.fromisoformat(st["started"])).total_seconds() > 3 * 3600
    if not st or stale:
        await start_quiz(bot, uid)


async def morning(bot: Bot, uid: int):
    t = db.active_topic(uid)
    if not t or not t["started_on"]:
        return
    plan, log_, day = db.plan_of(t), db.plan_log(uid, t["id"]), db.plan_day(t)
    if not plan:
        return
    if day > len(plan):
        await send_plan_summary(bot, uid, t)
        return
    lines = [f"☀️ <b>{esc(t['title'])}</b> — {day}-kun / {len(plan)}"]
    lines += [f"• {esc(x)}" for x in plan[day - 1]["tasks"]]
    if not await send(bot, uid, "\n".join(lines)):
        return
    if day > 1 and (day - 1) not in log_:
        await send(bot, uid, f"Kechagi {day - 1}-kun belgilanmagan:", reply_markup=plan_kb(t["id"], day - 1))


async def evening(bot: Bot, uid: int):
    t = db.active_topic(uid)
    if not t or not t["started_on"]:
        return
    plan, log_, day = db.plan_of(t), db.plan_log(uid, t["id"]), db.plan_day(t)
    if not plan or day > len(plan) or day in log_:
        return
    lines = [f"🌙 Bugungi {day}-kun vazifalari bajarildimi?"]
    lines += [f"• {esc(x)}" for x in plan[day - 1]["tasks"]]
    await send(bot, uid, "\n".join(lines), reply_markup=plan_kb(t["id"], day))


async def broadcast(bot: Bot, job, name: str):
    users = db.audience()
    for u in users:
        try:
            await job(bot, u["id"])
        except Exception:
            log.exception("%s: %s uchun xato", name, u["id"])
        await asyncio.sleep(BROADCAST_PAUSE)
    log.info("%s: %s ta foydalanuvchi", name, len(users))


async def scheduler(bot: Bot):
    fired: set[str] = set()
    running: set[asyncio.Task] = set()   # tarqatish fon vazifalari (GC o'chirmasin)
    while True:
        try:
            n = db.now()
            hm, stamp = n.strftime("%H:%M"), n.strftime("%Y-%m-%d %H:%M")
            if stamp not in fired:
                fired = {s for s in fired if s.startswith(n.strftime("%Y-%m-%d"))}
                fired.add(stamp)
                jobs = []
                if hm in config.QUIZ_TIMES:
                    jobs.append((quiz_job, "quiz"))
                if hm == config.PLAN_MORNING:
                    jobs.append((morning, "morning"))
                if hm == config.PLAN_EVENING:
                    jobs.append((evening, "evening"))
                for job, name in jobs:
                    task = asyncio.create_task(broadcast(bot, job, name))
                    running.add(task)
                    task.add_done_callback(running.discard)
        except Exception:
            log.exception("scheduler xatosi")
        await asyncio.sleep(20)
