"""Tests for #16 follow-ups. Store, database helpers and send_text are faked; the
database-backed test runs only with TEST_DATABASE_URL (tests/conftest.py)."""
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest

from app import pipeline
from app.config import settings
from app.followup import scheduler
from app.followup.scheduler import (checkin_text, followup_now, handle_followup_reply, one_level_up, run_due,
                                    schedule_followup)
from app.models import CareLevel, CoverageType, Profile, User


class FakeStore:
    def __init__(self):
        self.convos = {}

    def get_conversation(self, user_id):
        return self.convos.get(user_id, ("new", {}))

    def set_conversation(self, user_id, state, context=None):
        old = self.convos.get(user_id, ("new", {}))[1]
        self.convos[user_id] = (state, old if context is None else context)


@pytest.fixture
def env(monkeypatch):
    store = FakeStore()
    calls = {"cancel": [], "insert": [], "sent": [], "marked": []}
    monkeypatch.setattr(scheduler, "_store", lambda: store)
    monkeypatch.setattr(scheduler, "_user_language", lambda uid: "en")
    monkeypatch.setattr(scheduler, "_cancel_pending", lambda uid: calls["cancel"].append(uid))
    monkeypatch.setattr(scheduler, "_insert_followup", lambda uid, topic, due: calls["insert"].append((uid, topic, due)))
    monkeypatch.setattr(scheduler, "_mark_sent", lambda fid, now: calls["marked"].append(fid))
    msg_mod = types.ModuleType("app.messaging")
    msg_mod.send_text = lambda phone, text: calls["sent"].append((phone, text))
    monkeypatch.setitem(sys.modules, "app.messaging", msg_mod)
    return store, calls


def person(lang="en"):
    u = User(id=uuid.uuid4(), phone="+15550001111", active_profile_id=None, language=lang)
    p = Profile(id=uuid.uuid4(), user_id=u.id, label="uninsured", coverage_type=CoverageType.UNINSURED, language=lang)
    return u, p


def after_text2(store, u, level="urgent_care"):
    store.set_conversation(u.id, "idle", {"symptom_text": "think i sprained my ankle", "last_level_of_care": level,
                                          "recommended_location_ids": ["a"]})


def test_followup_now_checks_in_and_waits(env):
    store, calls = env
    u, _ = person()
    after_text2(store, u)
    out = followup_now(u.id)
    assert out == ['Checking in about "think i sprained my ankle". How are you feeling now? '
                   "Reply 1 better, 2 same, 3 worse."]
    assert store.convos[u.id][0] == "awaiting_followup_reply"
    assert store.convos[u.id][1]["last_level_of_care"] == "urgent_care"  # context kept
    assert calls["cancel"] == [u.id]


def test_followup_now_without_a_question(env):
    store, _ = env
    u, _ = person()
    assert "Nothing to follow up on yet" in followup_now(u.id)[0]


@pytest.mark.parametrize("reply,key", [("1", "Glad you're feeling better"), ("better", "Glad"), ("mejor", "Glad"),
                                       ("2", "Thanks for letting me know"), ("same", "Thanks")])
def test_better_and_same_go_idle(env, reply, key):
    store, _ = env
    u, p = person()
    after_text2(store, u)
    store.set_conversation(u.id, "awaiting_followup_reply")
    assert handle_followup_reply(u, p, reply)[0].startswith(key)
    assert store.convos[u.id] == ("idle", {})


def test_worse_goes_one_level_up_and_asks_location(env):
    store, _ = env
    u, p = person()
    after_text2(store, u, level="primary_care")
    store.set_conversation(u.id, "awaiting_followup_reply")
    out = handle_followup_reply(u, p, "3")
    assert out[0].endswith("Where are you right now?")
    assert store.convos[u.id] == ("awaiting_location", {"symptom_text": "think i sprained my ankle",
                                                        "level_of_care": "urgent_care", "level_confidence": 1.0})


def test_worse_from_urgent_care_points_to_er(env):
    store, _ = env
    u, p = person()
    after_text2(store, u, level="urgent_care")
    store.set_conversation(u.id, "awaiting_followup_reply")
    out = handle_followup_reply(u, p, "it's worse")
    assert "ER" in out[0] and "911" in out[0]
    assert store.convos[u.id][1]["level_of_care"] == "er"


def test_worse_after_self_care_is_seen_in_person(env):
    store, _ = env
    u, p = person()
    after_text2(store, u, level="self_care")
    store.set_conversation(u.id, "awaiting_followup_reply")
    handle_followup_reply(u, p, "3")
    assert store.convos[u.id][1]["level_of_care"] == "primary_care"


def test_spanish_reply(env):
    store, _ = env
    u, p = person("es")
    after_text2(store, u)
    store.set_conversation(u.id, "awaiting_followup_reply")
    assert handle_followup_reply(u, p, "peor")[0].startswith("Lamento que esté peor")


def test_new_question_goes_to_pipeline(env, monkeypatch):
    store, _ = env
    u, p = person()
    after_text2(store, u)
    store.set_conversation(u.id, "awaiting_followup_reply")
    monkeypatch.setattr(pipeline, "first_reply", lambda msg, profile: [f"FIRST:{msg.text}"])
    assert handle_followup_reply(u, p, "now my wrist hurts too") == ["FIRST:now my wrist hurts too"]


def test_short_unclear_reply_reprompts(env):
    store, _ = env
    u, p = person()
    after_text2(store, u)
    store.set_conversation(u.id, "awaiting_followup_reply")
    assert handle_followup_reply(u, p, "hmm")[0].startswith("Reply 1")
    assert store.convos[u.id][0] == "awaiting_followup_reply"


def test_run_due_sends_marks_and_waits(env, monkeypatch):
    store, calls = env
    uid = uuid.uuid4()
    fid = uuid.uuid4()
    monkeypatch.setattr(scheduler, "_due_rows", lambda now: [
        {"id": fid, "user_id": uid, "topic": "think i sprained my ankle", "phone": "+15550002222", "language": "es"}])
    assert run_due() == 1
    assert calls["sent"] == [("+15550002222", 'Te escribo sobre "think i sprained my ankle". ¿Cómo te sientes ahora? '
                                              "Responde 1 mejor, 2 igual, 3 peor.")]
    assert calls["marked"] == [fid]
    assert store.convos[uid][0] == "awaiting_followup_reply"


def test_run_due_send_failure_not_marked(env, monkeypatch):
    store, calls = env
    monkeypatch.setattr(scheduler, "_due_rows", lambda now: [
        {"id": uuid.uuid4(), "user_id": uuid.uuid4(), "topic": "x", "phone": "+1", "language": "en"}])
    sys.modules["app.messaging"].send_text = lambda phone, text: (_ for _ in ()).throw(RuntimeError("down"))
    assert run_due() == 0 and calls["marked"] == []


def test_schedule_followup_due_in_20_hours(env):
    _, calls = env
    uid = uuid.uuid4()
    before = datetime.now(timezone.utc)
    schedule_followup(uid, "ankle")
    (got_uid, topic, due), = calls["insert"]
    assert got_uid == uid and topic == "ankle"
    assert 19.9 * 3600 < (due - before).total_seconds() < 20.1 * 3600


def test_checkin_text_truncates_long_topics():
    text = checkin_text("word " * 40, "en")
    assert '..."' in text and len(text) < 200


def test_one_level_up():
    assert one_level_up(CareLevel.URGENT_CARE) is CareLevel.ER
    assert one_level_up(CareLevel.ER) is CareLevel.ER


@pytest.mark.skipif(not settings.database_url, reason="needs TEST_DATABASE_URL")
def test_db_schedule_respects_stop_and_one_pending():
    from app.conversation import store
    from app.db import get_conn
    u = store.get_or_create_user("+1555" + str(uuid.uuid4().int)[:7])
    try:
        schedule_followup(u.id, "first", hours=0)
        schedule_followup(u.id, "second", hours=0)
        with get_conn() as conn:
            rows = conn.execute("SELECT topic FROM followups WHERE user_id = %s AND sent_at IS NULL", (u.id,)).fetchall()
        assert [r["topic"] for r in rows] == ["second"]
        store.set_opted_out(u.id, True)
        schedule_followup(u.id, "third", hours=0)
        with get_conn() as conn:
            assert conn.execute("SELECT count(*) n FROM followups WHERE user_id = %s", (u.id,)).fetchone()["n"] == 0
    finally:
        store.delete_user(u.id)
