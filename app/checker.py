"""Javobni tekshirish: avval aniq moslik, to'liq gaplar uchun ixtiyoriy AI tekshiruv."""
import json
import logging
import re

import httpx

from . import config

log = logging.getLogger(__name__)
_PUNCT = re.compile(r"[.,!?;:\"«»„“”()\[\]…—–]")


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е").replace("’", "'")
    s = _PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def fill(prompt: str, a: str) -> str:
    return prompt.replace("___", a)


def expected_full(item) -> str:
    a = item["answers"][0]
    return fill(item["prompt"], a) if "___" in item["prompt"] else a


def _fix_span(prompt: str, answer: str):
    """Xato va to'g'ri gap so'zlari; boshi va oxiridagi bir xil so'zlar tashlangach qolgan oraliqlar."""
    pw, aw = norm(prompt).split(), norm(answer).split()
    short = min(len(pw), len(aw))
    a = 0
    while a < short and pw[a] == aw[a]:
        a += 1
    tail = 0
    while tail < short - a and pw[-1 - tail] == aw[-1 - tail]:
        tail += 1
    if a == len(aw) - tail:   # to'g'ri gapda yangi so'z yo'q: faqat ortiqcha so'z olib tashlangan
        return None
    return pw, aw, a, len(pw) - tail, len(aw) - tail


def fixed_words(prompt: str, answer: str) -> tuple[str, str] | None:
    """«Xatoni topish»: (xato bo'lak, to'g'ri bo'lak) yoki None. So'z yetishmasa xato bo'lak bo'sh."""
    sp = _fix_span(prompt, answer)
    if not sp:
        return None
    pw, aw, a, pb, ab = sp
    return " ".join(pw[a:pb]), " ".join(aw[a:ab])


def fix_candidates(prompt: str, answer: str) -> set[str]:
    """Tuzatilgan so'zni o'z ichiga olgan har qanday bo'lak: «с», «с работы», «пришёл с работы»."""
    sp = _fix_span(prompt, answer)
    if not sp:
        return set()
    _, aw, a, _, ab = sp
    return {" ".join(aw[i:j]) for i in range(a + 1) for j in range(ab, len(aw) + 1)}


def candidates(item) -> set[str]:
    """Qabul qilinadigan javoblar: javobning o'zi, to'ldirilgan so'z, predlog+so'z, to'liq gap.

    «Xatoni topish»da to'g'ri gap ham, tuzatilgan so'z yoki shu so'z qatnashgan bo'lak ham o'tadi."""
    p = item["prompt"]
    out = set()
    for a in item["answers"]:
        out.add(norm(a))
        if item.get("kind") == "fix":
            out |= fix_candidates(p, a)
        elif "___" in p:
            out.add(norm(fill(p, a)))
            m = re.search(r"(\S*)___(\S*)", p)
            if m:
                word = m.group(1) + a + m.group(2)
                out.add(norm(word))
                prev = re.search(r"(\S+)\s+\S*___", p)
                if prev:
                    out.add(norm(prev.group(1) + " " + word))
    return {c for c in out if c}


async def check(item, answer: str) -> dict:
    ok = bool(norm(answer)) and norm(answer) in candidates(item)
    res = {"correct": ok, "expected": item["answers"], "full": expected_full(item),
           "comment": None, "via": "exact"}
    if (not ok and norm(answer) and config.ANTHROPIC_API_KEY
            and item["kind"] in ("card", "translate", "sentence")):
        ai = await ai_check(item, answer)
        if ai:
            res.update(correct=ai["correct"], comment=ai["comment"] or None, via="ai")
    return res


async def ai_check(item, answer: str):
    system = (
        "Sen rus tili o'qituvchisisan. O'quvchi o'zbekchadan ruschaga tarjima qildi. "
        "Javob grammatik jihatdan to'g'ri va ma'nosi topshiriqqa mos bo'lsa correct=true. "
        "Padej qo'shimchalari va predloglarga qattiq e'tibor ber; kichik imlo xatosi bo'lsa "
        "ham correct=false va comment'da tuzatishni ko'rsat. Faqat JSON qaytar: "
        '{"correct": true yoki false, "comment": "o\'zbek tilida bitta qisqa gap"}')
    user = (f"Topshiriq (o'zbekcha): {item['prompt']}\n"
            f"Namuna javoblar: {'; '.join(item['answers'])}\n"
            f"O'quvchi javobi: {answer}")
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": config.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": config.ANTHROPIC_MODEL, "max_tokens": 200, "system": system,
                      "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", [])
                       if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0)) if m else {}
        return {"correct": bool(d.get("correct")), "comment": str(d.get("comment") or "")[:300]}
    except Exception as e:  # AI ishlamasa, oddiy tekshiruv natijasi qoladi
        log.warning("AI tekshiruv ishlamadi: %s", e)
        return None
