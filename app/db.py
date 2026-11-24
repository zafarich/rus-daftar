"""SQLite qatlami: mavzular, savollar, takrorlash (Leitner qutilari) va 7 kunlik reja."""
import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta

from . import config

KINDS = ("card", "translate", "gap", "ending", "choice", "sentence", "drill")
# Leitner: xato -> 0-quti (10 daqiqadan keyin qaytadi), to'g'ri -> keyingi quti
INTERVALS = [timedelta(minutes=10), timedelta(days=1), timedelta(days=2),
             timedelta(days=4), timedelta(days=8), timedelta(days=16), timedelta(days=32)]

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

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
CREATE TABLE IF NOT EXISTS progress(
  item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
  box INTEGER DEFAULT 0, due_at TEXT, correct INTEGER DEFAULT 0,
  wrong INTEGER DEFAULT 0, last_at TEXT);
CREATE TABLE IF NOT EXISTS plan_log(
  topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  day INTEGER NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(topic_id, day));
CREATE TABLE IF NOT EXISTS answers_log(
  id INTEGER PRIMARY KEY, item_id INTEGER, source TEXT, answer TEXT,
  correct INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
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


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(SCHEMA)
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
        activate_topic(slug)
    return {"slug": slug, "items": len(items), "removed": removed}


# ---------- o'qish ----------
def _item(r: dict | None) -> dict | None:
    if not r:
        return None
    r = dict(r)
    r["answers"] = json.loads(r.pop("answers_json"))
    r["options"] = json.loads(r.pop("options_json")) if r.get("options_json") else None
    r["extra"] = json.loads(r.pop("extra_json") or "{}")
    return r


def get_item(item_id: int):
    return _item(q1("SELECT i.*, p.box FROM items i LEFT JOIN progress p ON p.item_id=i.id "
                    "WHERE i.id=?", (item_id,)))


def topic_by_slug(slug):
    return q1("SELECT * FROM topics WHERE slug=?", (slug,))


def active_topic():
    return q1("SELECT * FROM topics WHERE active=1")


def list_topics():
    return q("SELECT t.id, t.slug, t.title, t.active, t.started_on, COUNT(i.id) AS n_items, "
             "SUM(CASE WHEN p.box>=3 THEN 1 ELSE 0 END) AS learned "
             "FROM topics t LEFT JOIN items i ON i.topic_id=t.id "
             "LEFT JOIN progress p ON p.item_id=i.id GROUP BY t.id ORDER BY t.sort, t.id")


def topic_items(topic_id):
    return [_item(r) for r in q(
        "SELECT i.*, p.box FROM items i LEFT JOIN progress p ON p.item_id=i.id "
        "WHERE i.topic_id=? ORDER BY i.pos", (topic_id,))]


def activate_topic(slug, restart=False):
    t = topic_by_slug(slug)
    if not t:
        raise TopicError(f"«{slug}» mavzusi topilmadi")
    with _lock:
        x("UPDATE topics SET active=0")
        x("UPDATE topics SET active=1 WHERE id=?", (t["id"],))
        if restart or not t["started_on"]:
            x("UPDATE topics SET started_on=? WHERE id=?", (today().isoformat(), t["id"]))
            if restart:
                x("DELETE FROM plan_log WHERE topic_id=?", (t["id"],))
                kv_del(f"plan_done_{t['id']}")
    return topic_by_slug(slug)


# ---------- 7 kunlik reja ----------
def plan_of(t) -> list:
    return json.loads(t["plan_json"] or "[]")


def plan_day(t, d: date | None = None):
    if not t or not t["started_on"]:
        return None
    return ((d or today()) - date.fromisoformat(t["started_on"])).days + 1


def plan_log(topic_id) -> dict[int, str]:
    return {r["day"]: r["status"] for r in q("SELECT day,status FROM plan_log WHERE topic_id=?",
                                             (topic_id,))}


def set_plan(topic_id, day, status):
    if status not in ("done", "missed"):
        raise TopicError("status: done yoki missed")
    x("INSERT INTO plan_log(topic_id,day,status,at) VALUES(?,?,?,?) "
      "ON CONFLICT(topic_id,day) DO UPDATE SET status=excluded.status, at=excluded.at",
      (topic_id, day, status, iso(now())))


# ---------- takrorlash ----------
def next_item(exclude=(), prefer_other=False):
    act = active_topic()
    aid = act["id"] if act else -1
    n = iso(now())
    ex = list(exclude) or [-1]
    ph = ",".join("?" * len(ex))
    base = ("SELECT i.*, p.box FROM items i LEFT JOIN progress p ON p.item_id=i.id "
            f"WHERE i.id NOT IN ({ph})")
    if prefer_other and aid != -1:
        r = q1(base + " AND i.topic_id<>? AND p.due_at<=? ORDER BY p.due_at LIMIT 1", (*ex, aid, n))
        if r:
            return _item(r)
    r = q1(base + " AND (p.due_at<=? OR (p.due_at IS NULL AND (i.topic_id=? OR ?=-1))) "
           "ORDER BY (i.topic_id=?) DESC, "
           "CASE WHEN p.box=0 THEN 0 WHEN p.box IS NULL THEN 1 ELSE 2 END, RANDOM() LIMIT 1",
           (*ex, n, aid, aid, aid))
    if r:
        return _item(r)
    # hammasi keyinga rejalashtirilgan bo'lsa ham mashq to'xtamasin
    return _item(q1(base + " AND i.topic_id=? ORDER BY p.due_at LIMIT 1", (*ex, aid)))


def review(item_id, correct: bool, source="web", answer=""):
    p = q1("SELECT * FROM progress WHERE item_id=?", (item_id,))
    box = p["box"] if p else 0
    box = min(box + 1, len(INTERVALS) - 1) if correct else 0
    t = now()
    x("INSERT INTO progress(item_id,box,due_at,correct,wrong,last_at) VALUES(?,?,?,?,?,?) "
      "ON CONFLICT(item_id) DO UPDATE SET box=excluded.box, due_at=excluded.due_at, "
      "correct=progress.correct+excluded.correct, wrong=progress.wrong+excluded.wrong, "
      "last_at=excluded.last_at",
      (item_id, box, iso(t + INTERVALS[box]), int(correct), int(not correct), iso(t)))
    x("INSERT INTO answers_log(item_id,source,answer,correct,at) VALUES(?,?,?,?,?)",
      (item_id, source, answer, int(correct), iso(t)))
    return box


def stats():
    d0 = today().isoformat()
    d7 = (today() - timedelta(days=6)).isoformat()
    t = q1("SELECT COUNT(*) n, COALESCE(SUM(correct),0) ok FROM answers_log WHERE at>=?", (d0,))
    w = q1("SELECT COUNT(*) n, COALESCE(SUM(correct),0) ok FROM answers_log WHERE at>=?", (d7,))
    return {"today": t, "week": w, "topics": list_topics()}
