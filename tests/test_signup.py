"""Signup page: Sendblue, store and messaging faked."""
import sys
import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import signup
from app.conversation import store
from app.models import User


@pytest.fixture
def env(monkeypatch):
    sent, states = [], {}
    signup._recent.clear()
    monkeypatch.setattr(signup, "register_contact", lambda phone: False)
    u = User(id=uuid.uuid4(), phone="+16195550123", active_profile_id=None, language="en")
    monkeypatch.setattr(store, "get_or_create_user", lambda phone: u)
    monkeypatch.setattr(store, "get_conversation", lambda uid: states.get(uid, ("new", {})))
    monkeypatch.setattr(store, "set_conversation", lambda uid, s, c=None: states.__setitem__(uid, (s, c)))
    msg = types.ModuleType("app.messaging")
    msg.send_text = lambda phone, text: sent.append((phone, text))
    monkeypatch.setitem(sys.modules, "app.messaging", msg)
    app = FastAPI()
    app.include_router(signup.router)
    return TestClient(app), sent, states, u, msg


@pytest.mark.parametrize("raw,norm", [("(619) 555-0123", "+16195550123"), ("+1 619.555.0123", "+16195550123"),
                                      ("6195550123", "+16195550123"), ("555-0123", None), ("0195550123", None)])
def test_normalize_us(raw, norm):
    assert signup.normalize_us(raw) == norm


def test_page(env):
    c, *_ = env
    assert "Our AI agent will text you for onboarding steps" in c.get("/start").text


def test_signup_texts_steps_and_starts_onboarding(env):
    c, sent, states, u, _ = env
    r = c.post("/start", json={"phone": "(619) 555-0123"})
    assert r.json()["texted"] is True
    assert sent[0][0] == "+16195550123" and "insurance card" in sent[0][1] and "household" in sent[0][1]
    assert len(sent) == 2 and states[u.id][0] == "onboarding_card"


def test_send_failure_says_text_hi(env):
    c, sent, states, u, msg = env
    msg.send_text = lambda phone, text: (_ for _ in ()).throw(RuntimeError("not verified"))
    r = c.post("/start", json={"phone": "6195550123"})
    assert r.json()["texted"] is False and u.id not in states


def test_bad_number(env):
    c, *_ = env
    assert c.post("/start", json={"phone": "123"}).status_code == 400


def test_no_double_send(env):
    c, sent, *_ = env
    c.post("/start", json={"phone": "6195550123"})
    c.post("/start", json={"phone": "6195550123"})
    assert len(sent) == 2
