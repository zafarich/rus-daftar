"""Veb-daftar: statik sahifa + JSON API.

Kirish ikki yo'l bilan:
  - Telegram Mini App: sahifa har so'rovda X-Telegram-Init-Data sarlavhasini yuboradi (imzo tekshiriladi);
  - brauzer: botdagi shaxsiy havola (/login?t=...) imzolangan cookie qo'yadi.
"""
import pathlib

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import auth, checker, config, db

STATIC = pathlib.Path(__file__).parent / "static"
COOKIE = "daftar_uid"
LOCKED = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
          "<title>Rus daftari</title><body style='font-family:system-ui;padding:2rem;max-width:32rem'>"
          "<h1>Rus daftari</h1><p>Havola eskirgan yoki noto'g'ri. Telegram botda <b>/daftar</b> "
          "buyrug'ini yuboring.</p>")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _user_id(req: Request) -> int | None:
    init_data = req.headers.get("x-telegram-init-data")
    if init_data:
        u = auth.parse_init_data(init_data)
        if not u:
            return None
        db.ensure_user(u["id"], u.get("first_name"), u.get("username"))
        return u["id"]
    uid = auth.uid_from_token(req.cookies.get(COOKIE, ""))
    if uid:
        db.ensure_user(uid)
    return uid


@app.middleware("http")
async def authenticate(req: Request, call_next):
    if req.url.path.startswith("/api/"):
        uid = _user_id(req)
        if not uid:
            return JSONResponse({"error": "auth"}, 401)
        req.state.uid = uid
    return await call_next(req)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login")
def login(t: str = ""):
    if not auth.uid_from_token(t):
        return HTMLResponse(LOCKED, 401)
    r = RedirectResponse("/", 303)
    r.set_cookie(COOKIE, t, max_age=180 * 86400, httponly=True,
                 secure=config.COOKIE_SECURE, samesite="lax")
    return r


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/me")
def me(req: Request):
    uid = req.state.uid
    u = db.q1("SELECT id, first_name, username FROM users WHERE id=?", (uid,))
    return {**u, "browser_url": auth.login_url(uid)}


@app.get("/api/topics")
def topics(req: Request):
    return db.list_topics(req.state.uid)


@app.get("/api/topics/{slug}")
def topic(slug: str, req: Request):
    uid = req.state.uid
    t = db.user_topic(uid, slug)
    if not t:
        raise HTTPException(404, "Mavzu topilmadi")
    return {
        "slug": t["slug"], "title": t["title"], "summary": t["summary"],
        "active": bool(t["active"]), "started_on": t["started_on"],
        "plan": db.plan_of(t), "log": db.plan_log(uid, t["id"]), "today": db.plan_day(t),
        "items": db.topic_items(uid, t["id"]),
    }


class CheckIn(BaseModel):
    item_id: int
    answer: str


@app.post("/api/check")
async def check(body: CheckIn, req: Request):
    it = db.get_item(body.item_id)
    if not it:
        raise HTTPException(404, "Savol topilmadi")
    res = await checker.check(it, body.answer)
    db.review(req.state.uid, it["id"], res["correct"], "web", body.answer)
    return res


class ReviewIn(BaseModel):
    item_id: int
    correct: bool


@app.post("/api/review")
def review(body: ReviewIn, req: Request):
    if not db.get_item(body.item_id):
        raise HTTPException(404, "Savol topilmadi")
    return {"box": db.review(req.state.uid, body.item_id, body.correct, "web-self")}


class PlanIn(BaseModel):
    slug: str
    day: int
    status: str


@app.post("/api/plan")
def plan(body: PlanIn, req: Request):
    t = db.topic_by_slug(body.slug)
    if not t:
        raise HTTPException(404, "Mavzu topilmadi")
    try:
        db.set_plan(req.state.uid, t["id"], body.day, body.status)
    except db.TopicError as e:
        raise HTTPException(400, str(e))
    return db.plan_log(req.state.uid, t["id"])


class ActIn(BaseModel):
    restart: bool = False


@app.post("/api/topics/{slug}/activate")
def activate(slug: str, body: ActIn, req: Request):
    try:
        db.activate_topic(req.state.uid, slug, restart=body.restart)
    except db.TopicError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
