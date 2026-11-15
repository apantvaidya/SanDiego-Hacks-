"""Next-day follow-up. 'Reply 1 better, 2 same, 3 worse.' Worse re-runs triage one level up. Owned by #16.

schedule_followup(user_id, topic, hours=20)
    Called by the pipeline (#17) after Text 2. One pending follow-up per user: older unsent
    ones are cancelled. Skipped for users who texted STOP.
run_due()
    Sends every due follow-up to users who have not opted out, marks it sent, sets state
    awaiting_followup_reply. Run it on a timer: start_background() starts a thread, or
    `uv run python -m app.followup.scheduler` sends once (for cron). Not wired into
    app/main.py (owned by #1); add `start_background()` to its lifespan to turn it on.
    On a messaging plan without outbound texts only #followup works, which is enough for
    the demo (it is a reply).
followup_now(user_id)
    #followup: the same check-in, sent as a reply right now; pending ones are cancelled.
handle_followup_reply(user, profile, text)
    1 better / 2 same: short reply, conversation cleared, idle.
    3 worse: one level above context.last_level_of_care, then "Where are you right now?"
    (state awaiting_location; the pipeline uses the saved level).
    Anything longer than a few words that is not 1/2/3 is a new question (pipeline.first_reply).

No Gemini here: the check-in text is fixed and quotes the user's own words back.
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.i18n.templates import t
from app.models import LEVEL_ORDER, CareLevel, InboundMessage, Profile, User

log = logging.getLogger(__name__)

TOPIC_CHARS = 60


def _store():
    from app.conversation import store
    return store


def _lang(language: str | None) -> str:
    return "es" if (language or "en").lower().startswith("es") else "en"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def _short(topic: str) -> str:
    topic = " ".join((topic or "").split())
    return topic if len(topic) <= TOPIC_CHARS else topic[:TOPIC_CHARS].rsplit(" ", 1)[0] + "..."


def checkin_text(topic: str, language: str) -> str:
    return t("followup.checkin", _lang(language), topic=_short(topic))


# --------------------------------------------------------------------------- database

def _db():
    from app.db import get_conn
    return get_conn()


def _insert_followup(user_id: UUID, topic: str, due_at: datetime) -> None:
    with _db() as conn:
        opted_out = conn.execute("SELECT opted_out FROM users WHERE id = %s", (user_id,)).fetchone()
        if not opted_out or opted_out["opted_out"]:
            return
        conn.execute("DELETE FROM followups WHERE user_id = %s AND sent_at IS NULL", (user_id,))
        conn.execute("INSERT INTO followups (user_id, topic, due_at) VALUES (%s, %s, %s)", (user_id, topic, due_at))


def _cancel_pending(user_id: UUID) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM followups WHERE user_id = %s AND sent_at IS NULL", (user_id,))


def _due_rows(now: datetime) -> list[dict]:
    with _db() as conn:
        return conn.execute(
            """SELECT f.id, f.user_id, f.topic, u.phone, u.language
                 FROM followups f JOIN users u ON u.id = f.user_id
                WHERE f.sent_at IS NULL AND f.due_at <= %s AND NOT u.opted_out
                ORDER BY f.due_at LIMIT 50""",
            (now,),
        ).fetchall()


def _mark_sent(followup_id: UUID, now: datetime) -> None:
    with _db() as conn:
        conn.execute("UPDATE followups SET sent_at = %s WHERE id = %s", (now, followup_id))


def _user_language(user_id: UUID) -> str:
    with _db() as conn:
        row = conn.execute("SELECT language FROM users WHERE id = %s", (user_id,)).fetchone()
    return row["language"] if row else "en"


# --------------------------------------------------------------------------- public

def schedule_followup(user_id: UUID, topic: str, hours: int = 20) -> None:
    _insert_followup(user_id, topic, datetime.now(timezone.utc) + timedelta(hours=hours))


def run_due() -> int:
    from app.messaging import send_text

    now = datetime.now(timezone.utc)
    sent = 0
    for row in _due_rows(now):
        try:
            send_text(row["phone"], checkin_text(row["topic"], row["language"]))
        except Exception:
            log.exception("follow-up send failed for %s", row["id"])
            continue
        _mark_sent(row["id"], now)
        _store().set_conversation(row["user_id"], "awaiting_followup_reply")  # keeps context
        sent += 1
    return sent


def followup_now(user_id: UUID) -> list[str]:
    store = _store()
    _state, context = store.get_conversation(user_id)
    lang = _lang(_user_language(user_id))
    topic = context.get("symptom_text")
    if not topic:
        return [t("followup.nothing_yet", lang)]
    _cancel_pending(user_id)
    store.set_conversation(user_id, "awaiting_followup_reply")  # keeps context
    return [checkin_text(topic, lang)]


_BETTER = {"1", "better", "mejor", "good", "fine", "bien"}
_SAME = {"2", "same", "igual", "the same", "lo mismo", "meh"}
_WORSE = {"3", "worse", "peor", "bad", "mal", "worst"}


def _answer(text: str) -> str | None:
    n = _norm(text)
    if n in _BETTER or n.startswith("1 "):
        return "better"
    if n in _SAME or n.startswith("2 "):
        return "same"
    if n in _WORSE or n.startswith("3 ") or "worse" in n or "peor" in n:
        return "worse"
    return None


def one_level_up(level: CareLevel) -> CareLevel:
    i = LEVEL_ORDER.index(level)
    return LEVEL_ORDER[min(i + 1, len(LEVEL_ORDER) - 1)]


def handle_followup_reply(user: User, profile: Profile, text: str) -> list[str]:
    """Called by the router in state awaiting_followup_reply. 1 better, 2 same, 3 worse.
    Worse re-runs triage one level above context.last_level_of_care. Set the next state via
    app.conversation.store.set_conversation."""
    store = _store()
    _state, context = store.get_conversation(user.id)
    lang = _lang(profile.language)
    answer = _answer(text)

    if answer in ("better", "same"):
        store.set_conversation(user.id, "idle", {})
        return [t(f"followup.{answer}", lang)]

    if answer == "worse":
        try:
            last = CareLevel(context.get("last_level_of_care") or CareLevel.URGENT_CARE.value)
        except ValueError:
            last = CareLevel.URGENT_CARE
        level = one_level_up(last)
        if level in (CareLevel.SELF_CARE, CareLevel.TELEHEALTH):
            level = CareLevel.PRIMARY_CARE  # worse means they should be seen in person
        topic = context.get("symptom_text") or text
        store.set_conversation(user.id, "awaiting_location", {
            "symptom_text": topic, "level_of_care": level.value, "level_confidence": 1.0,
        })
        key = "followup.worse_er" if level is CareLevel.ER else "followup.worse"
        return [t(key, lang)]

    if len(_norm(text).split()) >= 3:
        # A new question, not an answer: hand it to the normal flow.
        store.set_conversation(user.id, "idle", {})
        from app import pipeline
        return pipeline.first_reply(InboundMessage(phone=user.phone, text=text), profile)

    return [t("followup.reprompt", lang)]


def start_background(interval_s: int = 60) -> threading.Event:
    """Run run_due() every interval_s seconds in a daemon thread. Set the returned event to stop."""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval_s):
            try:
                n = run_due()
                if n:
                    log.info("sent %d follow-ups", n)
            except Exception:
                log.exception("run_due failed")

    threading.Thread(target=loop, name="followups", daemon=True).start()
    return stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"sent {run_due()} follow-ups")
