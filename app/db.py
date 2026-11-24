"""SQLite qatlami: mavzular, savollar, foydalanuvchilar, takrorlash (Leitner) va 7 kunlik reja.

Mavzu va savollar hamma uchun umumiy. Natijalar, reja va faol mavzu har bir Telegram
foydalanuvchisi uchun alohida saqlanadi (users.id = Telegram ID).
"""
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta

from . import config

log = logging.getLogger(__name__)

KINDS = ("card", "translate", "gap", "ending", "choice", "sentence", "drill")
# Leitner: xato -> 0-quti (10 daqiqadan keyin qaytadi), to'g'ri -> keyingi quti
INTERVALS = [timedelta(minutes=10), timedelta(days=1), timedelta(days=2),
             timedelta(days=4), timedelta(days=8), timedelta(days=16), timedelta(days=32)]

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

# topics.active — yangi foydalanuvchilarga beriladigan standart mavzu.
# topics.started_on — eski (bitta foydalanuvchili) sxemadan qolgan, ishlatilmaydi.
SCHEMA = """
CREATE TABLE IF NOT EXISTS topics(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  summary TEXT DEFAULT '', plan_json TEXT DEFAULT '[]', sort INTEGER DEFAULT 0,
  active INTEGER DEFAULT 0, started_on TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  key TEXT NOT NULL, kind TEXT NOT NULL, prompt TEXT NOT NULL,
  answers_json TEXT NOT NULL, options_json TEXT, hint TEXT, note TEXT,
  extra_json TEXT, pos INTEGER DEFAULT 0, UNIQUE(topic_id, key));
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, first_name TEXT, username TEXT,
  topic_id INTEGER REFERENCES topics(id) ON DELETE SET NULL,
  auto INTEGER DEFAULT 1, blocked INTEGER DEFAULT 0, created_at TEXT, seen_at TEXT);
CREATE TABLE IF NOT EXISTS user_topics(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  started_on TEXT, PRIMARY KEY(user_id, topic_id));
CREATE TABLE IF NOT EXISTS progress(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  box INTEGER DEFAULT 0, due_at TEXT, correct INTEGER DEFAULT 0,
  wrong INTEGER DEFAULT 0, last_at TEXT, PRIMARY KEY(user_id, item_id));
CREATE INDEX IF NOT EXISTS progress_due ON progress(user_id, due_at);
CREATE TABLE IF NOT EXISTS plan_log(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  day INTEGER NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(user_id, topic_id, day));
CREATE TABLE IF NOT EXISTS answers_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, item_id INTEGER, source TEXT, answer TEXT,
  correct INTEGER, at TEXT);
CREATE INDEX IF NOT EXISTS answers_user_at ON answers_log(user_id, at);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)
"""


class TopicError(ValueError):
    pass


# ---------- umumiy ----------
def now() -> datetime:
    return datetime.now(config.TZ)


def today() -> date:
    return now().date()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _statements():
    return [s.strip() for s in SCHEMA.split(";") if s.strip()]


def _cols(c, table) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _migrate_single_user(c: sqlite3.Connection):
    """Eski bitta foydalanuvchili bazani ko'p foydalanuvchiliga o'tkazadi.

    Eski natijalar, reja belgilari va javoblar OWNER_ID ga yoziladi. OWNER_ID bo'lmasa,
    ular o'chadi (mavzu va savollar saqlanadi).
    """
    if not _cols(c, "progress") or "user_id" in _cols(c, "progress"):
        return
    owner = config.OWNER_ID
    ts = iso(now())
    c.execute("PRAGMA foreign_keys=OFF")
    try:
        c.execute("BEGIN")
        for t in ("progress", "plan_log", "answers_log"):
            c.execute(f"ALTER TABLE {t} RENAME TO {t}_v1")
        for s in _statements():
            c.execute(s)
        if owner:
            d = c.execute("SELECT id FROM topics WHERE active=1").fetchone()
            c.execute("INSERT INTO users(id, topic_id, created_at, seen_at) VALUES(?,?,?,?)",
                      (owner, d[0] if d else None, ts, ts))
            c.execute("INSERT INTO user_topics(user_id, topic_id, started_on) "
                      "SELECT ?, id, started_on FROM topics WHERE started_on IS NOT NULL", (owner,))
            c.execute("INSERT INTO progress(user_id,item_id,box,due_at,correct,wrong,last_at) "
                      "SELECT ?, item_id, box, due_at, correct, wrong, last_at FROM progress_v1", (owner,))
            c.execute("INSERT INTO plan_log(user_id,topic_id,day,status,at) "
                      "SELECT ?, topic_id, day, status, at FROM plan_log_v1", (owner,))
            c.execute("INSERT INTO answers_log(user_id,item_id,source,answer,correct,at) "
                      "SELECT ?, item_id, source, answer, correct, at FROM answers_log_v1", (owner,))
            c.execute("UPDATE kv SET key='quiz:' || ? WHERE key='quiz'", (owner,))
            c.execute("UPDATE kv SET key='plan_done:' || ? || ':' || substr(key, 11) "
                      "WHERE key LIKE 'plan\\_done\\_%' ESCAPE '\\'", (owner,))
        else:
            c.execute("DELETE FROM kv WHERE key='quiz' OR key LIKE 'plan\\_done\\_%' ESCAPE '\\'")
        n = c.execute("SELECT COUNT(*) FROM progress_v1").fetchone()[0]
        for t in ("progress", "plan_log", "answers_log"):
            c.execute(f"DROP TABLE {t}_v1")
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.execute("PRAGMA foreign_keys=ON")
    if owner:
        log.info("Baza ko'p foydalanuvchiliga o'tkazildi: %s ta natija %s ga yozildi", n, owner)
    else:
        log.warning("Baza ko'p foydalanuvchiliga o'tkazildi; OWNER_ID yo'qligi uchun %s ta eski natija o'chdi", n)


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
            c = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            _migrate_single_user(c)
            c.execute("PRAGMA foreign_keys=ON")
            for s in _statements():
                c.execute(s)
            c.commit()
            _conn = c
        return _conn


def q(sql, p=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, p).fetchall()]


def q1(sql, p=()):
    r = q(sql, p)
    return r[0] if r else None


def x(sql, p=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, p)
        c.commit()
        return cur


def kv_get(k, default=None):
    r = q1("SELECT value FROM kv WHERE key=?", (k,))
    return json.loads(r["value"]) if r else default


def kv_set(k, v):
    x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (k, json.dumps(v, ensure_ascii=False)))


def kv_del(k):
    x("DELETE FROM kv WHERE key=?", (k,))


# ---------- mavzu importi ----------
def _key(it) -> str:
    ex = it.get("extra") or {}
    raw = "|".join([it["kind"], it["prompt"], ex.get("base") or "", ex.get("uz") or ""])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _items_from(data: dict) -> list[dict]:
    out = []

    def add(kind, prompt, answers, options=None, hint=None, note=None, extra=None):
        if kind not in KINDS:
            raise TopicError(f"Noma'lum tur: {kind}")
        prompt = (prompt or "").strip()
        if not prompt:
            raise TopicError(f"{kind}: prompt bo'sh")
        if isinstance(answers, str):
            answers = [answers]
        answers = [a.strip() for a in (answers or []) if a and a.strip()]
        if not answers:
            raise TopicError(f"{kind}: «{prompt}» uchun javob yo'q")
        if kind in ("gap", "ending", "drill") and "___" not in prompt:
            raise TopicError(f"{kind}: «{prompt}» ichida ___ yo'q")
        if kind == "choice":
            if not options or len(options) < 2:
                raise TopicError(f"choice: «{prompt}» uchun kamida 2 ta variant kerak")
            if not any(a in options for a in answers):
                raise TopicError(f"choice: «{prompt}» javobi variantlar ichida yo'q")
        out.append(dict(kind=kind, prompt=prompt, answers=answers, options=options,
                        hint=hint, note=note, extra=extra or {}))

    for c in data.get("cards", []):
        add("card", c.get("front"), c.get("back"), hint=c.get("hint"), note=c.get("note"))
    for e in data.get("exercises", []):
        add(e.get("type"), e.get("prompt"), e.get("answers"), e.get("options"),
            e.get("hint"), e.get("note"), e.get("extra"))
    for d in data.get("drills", []):
        for it in d.get("items", []):
            add("drill", d.get("frame"), it.get("answer"), hint=d.get("hint"), note=it.get("note"),
                extra={"title": d.get("title"), "uz": it.get("uz"), "base": it.get("base")})
    return out


def import_topic(data: dict, activate: bool = False) -> dict:
    slug, title = data.get("slug"), data.get("title")
    if not slug or not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise TopicError("slug faqat kichik lotin harf, raqam, - va _ dan iborat bo'lsin")
    if not title:
        raise TopicError("title yo'q")
    items = _items_from(data)
    plan = data.get("plan", [])
    for i, d in enumerate(plan):
        if not isinstance(d.get("tasks"), list) or not d["tasks"]:
            raise TopicError(f"plan[{i}]: tasks ro'yxati bo'sh")
    keys = [_key(it) for it in items]
    dups = {k for k in keys if keys.count(k) > 1}
    if dups:
        bad = [it["prompt"] for it, k in zip(items, keys) if k in dups]
        raise TopicError(f"Takroriy savollar: {bad[:5]}")

    ts = iso(now())
    with _lock:
        c = conn()
        try:
            c.execute(
                "INSERT INTO topics(slug,title,summary,plan_json,sort,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET title=excluded.title, "
                "summary=excluded.summary, plan_json=excluded.plan_json, sort=excluded.sort, "
                "updated_at=excluded.updated_at",
                (slug, title, data.get("summary", ""), json.dumps(plan, ensure_ascii=False),
                 int(data.get("sort", 0)), ts, ts))
            tid = c.execute("SELECT id FROM topics WHERE slug=?", (slug,)).fetchone()[0]
            for pos, (it, k) in enumerate(zip(items, keys)):
                c.execute(
                    "INSERT INTO items(topic_id,key,kind,prompt,answers_json,options_json,hint,note,"
                    "extra_json,pos) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(topic_id,key) DO UPDATE SET "
                    "kind=excluded.kind, prompt=excluded.prompt, answers_json=excluded.answers_json, "
                    "options_json=excluded.options_json, hint=excluded.hint, note=excluded.note, "
                    "extra_json=excluded.extra_json, pos=excluded.pos",
                    (tid, k, it["kind"], it["prompt"], json.dumps(it["answers"], ensure_ascii=False),
                     json.dumps(it["options"], ensure_ascii=False) if it["options"] else None,
                     it["hint"], it["note"], json.dumps(it["extra"], ensure_ascii=False), pos))
            ph = ",".join("?" * len(keys)) or "''"
            removed = c.execute(f"DELETE FROM items WHERE topic_id=? AND key NOT IN ({ph})",
                                (tid, *keys)).rowcount
            c.commit()
        except Exception:
            c.rollback()
            raise
    if activate:
        set_default_topic(slug)
    return {"slug": slug, "items": len(items), "removed": removed}


# ---------- mavzular ----------
def _item(r: dict | None) -> dict | None:
    if not r:
        return None
    r = dict(r)
    r["answers"] = json.loads(r.pop("answers_json"))
    r["options"] = json.loads(r.pop("options_json")) if r.get("options_json") else None
    r["extra"] = json.loads(r.pop("extra_json") or "{}")
    return r


def get_item(item_id: int):
    return _item(q1("SELECT * FROM items WHERE id=?", (item_id,)))


def topic_by_slug(slug):
    return q1("SELECT * FROM topics WHERE slug=?", (slug,))


def default_topic():
    """Yangi foydalanuvchiga beriladigan mavzu: manage.py bilan faol qilingani, bo'lmasa birinchisi."""
    return (q1("SELECT * FROM topics WHERE active=1")
            or q1("SELECT * FROM topics ORDER BY sort, id LIMIT 1"))


def set_default_topic(slug):
    t = topic_by_slug(slug)
    if not t:
        raise TopicError(f"«{slug}» mavzusi topilmadi")
    with _lock:
        x("UPDATE topics SET active=0")
        x("UPDATE topics SET active=1 WHERE id=?", (t["id"],))
    return topic_by_slug(slug)


def list_topics(uid: int | None = None):
    """Mavzular ro'yxati. uid berilsa: o'sha foydalanuvchining faol mavzusi va o'zlashtirgani."""
    rows = q("SELECT t.id, t.slug, t.title, t.active AS is_default, COUNT(i.id) AS n_items, "
             "SUM(CASE WHEN p.box>=3 THEN 1 ELSE 0 END) AS learned "
             "FROM topics t LEFT JOIN items i ON i.topic_id=t.id "
             "LEFT JOIN progress p ON p.item_id=i.id AND p.user_id=? "
             "GROUP BY t.id ORDER BY t.sort, t.id", (uid,))
    active_id = _active_topic_id(uid) if uid is not None else None
    for r in rows:
        r["active"] = int(r["id"] == active_id) if uid is not None else r["is_default"]
    return rows


def topic_items(uid: int, topic_id: int):
    return [_item(r) for r in q(
        "SELECT i.*, p.box FROM items i LEFT JOIN progress p ON p.item_id=i.id AND p.user_id=? "
        "WHERE i.topic_id=? ORDER BY i.pos", (uid, topic_id))]


# ---------- foydalanuvchilar ----------
def ensure_user(uid: int, first_name: str | None = None, username: str | None = None) -> dict:
    """Foydalanuvchini yaratadi (standart mavzu bilan, reja bugundan) yoki ma'lumotini yangilaydi."""
    ts = iso(now())
    with _lock:
        if q1("SELECT id FROM users WHERE id=?", (uid,)):
            x("UPDATE users SET first_name=COALESCE(?, first_name), username=COALESCE(?, username), "
              "blocked=0, seen_at=? WHERE id=?", (first_name, username, ts, uid))
        else:
            t = default_topic()
            x("INSERT INTO users(id, first_name, username, topic_id, created_at, seen_at) "
              "VALUES(?,?,?,?,?,?)", (uid, first_name, username, t["id"] if t else None, ts, ts))
            if t:
                _start_plan(uid, t["id"])
        return q1("SELECT * FROM users WHERE id=?", (uid,))


def audience():
    """Avtomatik xabar oladiganlar: botni bloklamagan va /off qilmaganlar."""
    return q("SELECT id FROM users WHERE blocked=0 AND auto=1 ORDER BY id")


def set_blocked(uid: int, blocked: bool = True):
    x("UPDATE users SET blocked=? WHERE id=?", (int(blocked), uid))


def set_auto(uid: int, on: bool):
    x("UPDATE users SET auto=? WHERE id=?", (int(on), uid))


def user_count() -> dict:
    return q1("SELECT COUNT(*) AS total, COALESCE(SUM(blocked=0 AND auto=1),0) AS auto, "
              "COALESCE(SUM(blocked),0) AS blocked FROM users")


def _active_topic_id(uid: int):
    u = q1("SELECT topic_id FROM users WHERE id=?", (uid,))
    if u and u["topic_id"]:
        return u["topic_id"]
    d = default_topic()
    return d["id"] if d else None


def _start_plan(uid: int, topic_id: int, restart: bool = False):
    if restart:
        x("INSERT INTO user_topics(user_id,topic_id,started_on) VALUES(?,?,?) "
          "ON CONFLICT(user_id,topic_id) DO UPDATE SET started_on=excluded.started_on",
          (uid, topic_id, today().isoformat()))
        x("DELETE FROM plan_log WHERE user_id=? AND topic_id=?", (uid, topic_id))
        kv_del(f"plan_done:{uid}:{topic_id}")
    else:
        x("INSERT INTO user_topics(user_id,topic_id,started_on) VALUES(?,?,?) "
          "ON CONFLICT(user_id,topic_id) DO UPDATE SET "
          "started_on=COALESCE(user_topics.started_on, excluded.started_on)",
          (uid, topic_id, today().isoformat()))


def user_topic(uid: int, slug: str | None = None):
    """Foydalanuvchi uchun mavzu (slug bo'lmasa — faol mavzusi): started_on va active shu odamniki."""
    active_id = _active_topic_id(uid)
    t = topic_by_slug(slug) if slug else (active_id and q1("SELECT * FROM topics WHERE id=?", (active_id,)))
    if not t:
        return None
    ut = q1("SELECT started_on FROM user_topics WHERE user_id=? AND topic_id=?", (uid, t["id"]))
    t = dict(t)
    t["started_on"] = ut["started_on"] if ut else None
    t["active"] = int(t["id"] == active_id)
    return t


def active_topic(uid: int):
    return user_topic(uid)


def activate_topic(uid: int, slug: str, restart: bool = False):
    t = topic_by_slug(slug)
    if not t:
        raise TopicError(f"«{slug}» mavzusi topilmadi")
    with _lock:
        x("UPDATE users SET topic_id=? WHERE id=?", (t["id"], uid))
        _start_plan(uid, t["id"], restart=restart)
    return user_topic(uid, slug)


# ---------- 7 kunlik reja ----------
def plan_of(t) -> list:
    return json.loads(t["plan_json"] or "[]")


def plan_day(t, d: date | None = None):
    if not t or not t["started_on"]:
        return None
    return ((d or today()) - date.fromisoformat(t["started_on"])).days + 1


def plan_log(uid: int, topic_id: int) -> dict[int, str]:
    return {r["day"]: r["status"] for r in q(
        "SELECT day,status FROM plan_log WHERE user_id=? AND topic_id=?", (uid, topic_id))}


def set_plan(uid: int, topic_id: int, day: int, status: str):
    if status not in ("done", "missed"):
        raise TopicError("status: done yoki missed")
    x("INSERT INTO plan_log(user_id,topic_id,day,status,at) VALUES(?,?,?,?,?) "
      "ON CONFLICT(user_id,topic_id,day) DO UPDATE SET status=excluded.status, at=excluded.at",
      (uid, topic_id, day, status, iso(now())))


# ---------- takrorlash ----------
def next_item(uid: int, exclude=(), prefer_other=False):
    aid = _active_topic_id(uid) or -1
    n = iso(now())
    ex = list(exclude) or [-1]
    ph = ",".join("?" * len(ex))
    base = ("SELECT i.*, p.box FROM items i LEFT JOIN progress p ON p.item_id=i.id AND p.user_id=? "
            f"WHERE i.id NOT IN ({ph})")
    if prefer_other and aid != -1:
        r = q1(base + " AND i.topic_id<>? AND p.due_at<=? ORDER BY p.due_at LIMIT 1",
               (uid, *ex, aid, n))
        if r:
            return _item(r)
    r = q1(base + " AND (p.due_at<=? OR (p.due_at IS NULL AND (i.topic_id=? OR ?=-1))) "
           "ORDER BY (i.topic_id=?) DESC, "
           "CASE WHEN p.box=0 THEN 0 WHEN p.box IS NULL THEN 1 ELSE 2 END, RANDOM() LIMIT 1",
           (uid, *ex, n, aid, aid, aid))
    if r:
        return _item(r)
    # hammasi keyinga rejalashtirilgan bo'lsa ham mashq to'xtamasin
    return _item(q1(base + " AND i.topic_id=? ORDER BY p.due_at LIMIT 1", (uid, *ex, aid)))


def review(uid: int, item_id: int, correct: bool, source="web", answer=""):
    p = q1("SELECT box FROM progress WHERE user_id=? AND item_id=?", (uid, item_id))
    box = p["box"] if p else 0
    box = min(box + 1, len(INTERVALS) - 1) if correct else 0
    t = now()
    x("INSERT INTO progress(user_id,item_id,box,due_at,correct,wrong,last_at) VALUES(?,?,?,?,?,?,?) "
      "ON CONFLICT(user_id,item_id) DO UPDATE SET box=excluded.box, due_at=excluded.due_at, "
      "correct=progress.correct+excluded.correct, wrong=progress.wrong+excluded.wrong, "
      "last_at=excluded.last_at",
      (uid, item_id, box, iso(t + INTERVALS[box]), int(correct), int(not correct), iso(t)))
    x("INSERT INTO answers_log(user_id,item_id,source,answer,correct,at) VALUES(?,?,?,?,?,?)",
      (uid, item_id, source, answer, int(correct), iso(t)))
    return box


def stats(uid: int):
    d0 = today().isoformat()
    d7 = (today() - timedelta(days=6)).isoformat()
    sql = "SELECT COUNT(*) n, COALESCE(SUM(correct),0) ok FROM answers_log WHERE user_id=? AND at>=?"
    return {"today": q1(sql, (uid, d0)), "week": q1(sql, (uid, d7)), "topics": list_topics(uid)}
