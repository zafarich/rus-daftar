"""Veb-daftar: statik sahifa + JSON API. Kirish botdagi havola orqali (cookie)."""
import pathlib
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import checker, config, db

STATIC = pathlib.Path(__file__).parent / "static"
COOKIE = "daftar_key"
LOCKED = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
          "<title>Rus daftari</title><body style='font-family:system-ui;padding:2rem;max-width:32rem'>"
          "<h1>Rus daftari</h1><p>Kirish uchun Telegram botda <b>/daftar</b> buyrug'ini yuboring "
          "va «Daftarni ochish» tugmasini bosing.</p>")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _authed(req: Request) -> bool:
    k = req.cookies.get(COOKIE, "")
    return bool(config.ACCESS_KEY) and secrets.compare_digest(k.encode(), config.ACCESS_KEY.encode())


@app.middleware("http")
async def auth(req: Request, call_next):
    if req.url.path in ("/login", "/healthz") or _authed(req):
        return await call_next(req)
    if req.url.path.startswith("/api/"):
        return JSONResponse({"error": "auth"}, 401)
    return HTMLResponse(LOCKED, 401)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login")
def login(key: str = ""):
    if not config.ACCESS_KEY or not secrets.compare_digest(key.encode(), config.ACCESS_KEY.encode()):
        return HTMLResponse(LOCKED, 401)
    r = RedirectResponse("/", 303)
    r.set_cookie(COOKIE, key, max_age=180 * 86400, httponly=True,
                 secure=config.COOKIE_SECURE, samesite="lax")
    return r


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/topics")
def topics():
    return db.list_topics()


@app.get("/api/topics/{slug}")
def topic(slug: str):
    t = db.topic_by_slug(slug)
    if not t:
        raise HTTPException(404, "Mavzu topilmadi")
    return {
        "slug": t["slug"], "title": t["title"], "summary": t["summary"],
        "active": bool(t["active"]), "started_on": t["started_on"],
        "plan": db.plan_of(t), "log": db.plan_log(t["id"]), "today": db.plan_day(t),
        "items": db.topic_items(t["id"]),
    }


class CheckIn(BaseModel):
    item_id: int
    answer: str


@app.post("/api/check")
async def check(body: CheckIn):
    it = db.get_item(body.item_id)
    if not it:
        raise HTTPException(404, "Savol topilmadi")
    res = await checker.check(it, body.answer)
    db.review(it["id"], res["correct"], "web", body.answer)
    return res


class ReviewIn(BaseModel):
    item_id: int
    correct: bool


@app.post("/api/review")
def review(body: ReviewIn):
    if not db.get_item(body.item_id):
        raise HTTPException(404, "Savol topilmadi")
    return {"box": db.review(body.item_id, body.correct, "web-self")}


class PlanIn(BaseModel):
    slug: str
    day: int
    status: str


@app.post("/api/plan")
def plan(body: PlanIn):
    t = db.topic_by_slug(body.slug)
    if not t:
        raise HTTPException(404, "Mavzu topilmadi")
    try:
        db.set_plan(t["id"], body.day, body.status)
    except db.TopicError as e:
        raise HTTPException(400, str(e))
    return db.plan_log(t["id"])


class ActIn(BaseModel):
    restart: bool = False


@app.post("/api/topics/{slug}/activate")
def activate(slug: str, body: ActIn):
    try:
        db.activate_topic(slug, restart=body.restart)
    except db.TopicError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}
