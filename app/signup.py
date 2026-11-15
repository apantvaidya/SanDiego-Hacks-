"""Signup page: enter a phone number, get texted the onboarding steps.

GET  /start   one field for the number
POST /start   {"phone": "..."} -> register the number as a Sendblue verified contact, then
              text the welcome + what onboarding uses, and set state onboarding_card so the
              reply (card photo, NONE, MEDI-CAL) goes straight into onboarding.
              On Sendblue's free plan a number must text the line once before we can text it;
              if the send fails, the page says to text "hi" to the line, and onboarding starts
              from that text as usual.
"""
from __future__ import annotations

import logging
import re
import threading
import time

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter()

RESEND_AFTER_S = 120
_recent: dict[str, float] = {}
_lock = threading.Lock()

INTRO = ("Hi from Care Navigator. I'll set you up by text in about 2 minutes. I can use: "
         "a photo of the front of your insurance card (or reply NONE if uninsured, MEDI-CAL, or OTHER), "
         "how many people are in your household, a yearly income range (optional, it unlocks clinic "
         "discounts), and English or Spanish. I never keep your card photo or member ID. "
         "This is not for emergencies: call 911.")


def normalize_us(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01":
        return None
    return "+1" + digits


def _line() -> str:
    return settings.sendblue_from_number or ""


def register_contact(phone: str) -> bool:
    """Add the number as a Sendblue verified contact. Returns True if already verified."""
    resp = httpx.post(
        "https://api.sendblue.co/v3/verified-contacts",
        headers={"sb-api-key-id": settings.sendblue_api_key or "", "sb-api-secret-key": settings.sendblue_api_secret or ""},
        json={"phone_number": phone},
        timeout=15,
    )
    if resp.status_code >= 400:
        log.warning("verified-contacts %s: %s", resp.status_code, resp.text[:200])
        return False
    contact = (resp.json().get("data") or {}).get("contact") or {}
    return bool(contact.get("verified"))


def start_onboarding(phone: str) -> bool:
    """Text the intro and the card question. True if the texts went out."""
    from app.conversation import store
    from app.conversation.onboarding import _welcome
    from app.messaging import send_text

    user = store.get_or_create_user(phone)
    state, _ = store.get_conversation(user.id)
    if state not in ("new", "onboarding_card"):  # already set up: don't restart them
        send_text(phone, "You're already set up with Care Navigator. Text me any health question.")
        return True
    send_text(phone, INTRO)
    send_text(phone, _welcome("en"))
    store.set_conversation(user.id, "onboarding_card", {"onb": {"lang": "en"}})
    return True


class StartIn(BaseModel):
    phone: str


@router.post("/start")
def start(body: StartIn) -> dict:
    phone = normalize_us(body.phone)
    if phone is None:
        raise HTTPException(400, "Enter a US mobile number, like (619) 555-0123.")
    now = time.monotonic()
    with _lock:
        if now - _recent.get(phone, -1e9) < RESEND_AFTER_S:
            return {"ok": True, "texted": True, "line": _line()}
        _recent[phone] = now
    try:
        register_contact(phone)
    except Exception:
        log.exception("could not register %s with Sendblue", phone[-4:])
    try:
        texted = start_onboarding(phone)
    except Exception:
        log.warning("could not text %s yet (free plan: they must text the line first)", phone[-4:])
        texted = False
    return {"ok": True, "texted": texted, "line": _line()}


@router.get("/start", response_class=HTMLResponse)
def page() -> str:
    return PAGE


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Care Navigator</title>
<style>
 :root{--bg:#f7f7f8;--card:#fff;--ink:#1c1c1e;--muted:#6e6e73;--line:#d1d1d6;--accent:#0a84ff}
 @media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--ink:#f2f2f7;--muted:#98989f;--line:#38383a}}
 *{box-sizing:border-box} body{margin:0;min-height:100dvh;display:grid;place-items:center;background:var(--bg);color:var(--ink);
 font:17px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:16px}
 main{width:100%;max-width:400px;background:var(--card);border:1px solid var(--line);border-radius:20px;padding:28px 24px}
 h1{font-size:24px;margin:0 0 6px} p{margin:0 0 20px;color:var(--muted)}
 label{display:block;font-size:14px;margin-bottom:6px}
 input{width:100%;font:inherit;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink)}
 input:focus{outline:2px solid var(--accent);outline-offset:-1px}
 button{width:100%;margin-top:12px;font:inherit;font-weight:600;padding:12px;border:0;border-radius:12px;background:var(--accent);color:#fff;cursor:pointer}
 button:disabled{opacity:.5} #msg{margin:16px 0 0;color:var(--ink)} small{display:block;margin-top:18px;color:var(--muted);font-size:13px}
</style></head><body><main>
<h1>Care Navigator</h1>
<p>Enter your phone number. Our AI agent will text you for onboarding steps.</p>
<form id="f"><label for="phone">Mobile number</label>
<input id="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="(619) 555-0123" required>
<button id="b" type="submit">Text me</button></form>
<p id="msg" role="status"></p>
<small>Not for emergencies. Call 911.</small>
</main><script>
const f=document.getElementById('f'),b=document.getElementById('b'),m=document.getElementById('msg');
const fmt=n=>n.replace(/^\\+1(\\d{3})(\\d{3})(\\d{4})$/,'($1) $2-$3');
f.addEventListener('submit',async e=>{e.preventDefault();b.disabled=true;m.textContent='Sending...';
 try{const r=await fetch('start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:document.getElementById('phone').value})});
  const d=await r.json(); if(!r.ok) throw new Error(d.detail||'Something went wrong');
  m.textContent=d.texted?'Check your messages. Our AI agent just texted you.'
   :'Almost there: text "hi" to '+fmt(d.line)+' and our AI agent will start your onboarding.';
 }catch(err){m.textContent=err.message}finally{b.disabled=false}});
</script></body></html>"""
