"""Tests for #4: Gemini wrapper and triage.

Unit tests mock Gemini. Live tests call the real API and run only with RUN_LIVE_GEMINI=1:
    RUN_LIVE_GEMINI=1 uv run pytest tests/test_triage.py
"""
import os
import uuid

import pytest

from app.llm import gemini
from app.llm.gemini import GeminiError
from app.models import CareLevel, CoverageType, Profile
from app.triage import assess
from app.triage.assess import LOCATION_QUESTION, bump_if_unsure, first_aid_reply, level_of_care


def _profile(lang="en"):
    return Profile(id=uuid.uuid4(), user_id=uuid.uuid4(), label="uninsured",
                   coverage_type=CoverageType.UNINSURED, language=lang)


# ------------------------------------------------------------------ bump_if_unsure (pure)

@pytest.mark.parametrize("level,conf,expected", [
    (CareLevel.SELF_CARE, 0.9, CareLevel.SELF_CARE),
    (CareLevel.SELF_CARE, 0.5, CareLevel.TELEHEALTH),
    (CareLevel.TELEHEALTH, 0.69, CareLevel.PRIMARY_CARE),
    (CareLevel.PRIMARY_CARE, 0.1, CareLevel.URGENT_CARE),
    (CareLevel.URGENT_CARE, 0.6, CareLevel.ER),
    (CareLevel.URGENT_CARE, 0.7, CareLevel.URGENT_CARE),  # threshold itself is sure enough
    (CareLevel.ER, 0.0, CareLevel.ER),                    # ER stays ER
])
def test_bump_if_unsure(level, conf, expected):
    assert bump_if_unsure(level, conf) is expected


def test_bump_custom_threshold():
    assert bump_if_unsure(CareLevel.URGENT_CARE, 0.8, threshold=0.9) is CareLevel.ER


# ------------------------------------------------------------------ first_aid_reply (mocked)

def _mock_json(monkeypatch, out=None, exc=None):
    calls = []

    def fake(prompt, schema, image=None, system=None):
        calls.append({"prompt": prompt, "schema": schema, "system": system})
        if exc:
            raise exc
        return out

    monkeypatch.setattr(assess, "generate_json", fake)
    return calls


def test_first_aid_appends_location_question(monkeypatch):
    _mock_json(monkeypatch, {"red_flag": False, "red_flag_category": "none", "reply_language": "en",
                             "reply": "Rest and ice it. Go to the ER or call 911 if: you can't stand."})
    r = first_aid_reply("think i sprained my ankle", _profile())
    assert not r.red_flag
    assert r.reply.endswith(LOCATION_QUESTION["en"])
    assert "Rest and ice it." in r.reply


def test_first_aid_strips_markdown_and_duplicate_question(monkeypatch):
    _mock_json(monkeypatch, {"red_flag": False, "red_flag_category": "none", "reply_language": "en",
                             "reply": "**Rest** it. Where are you right now?"})
    r = first_aid_reply("ankle", _profile())
    assert "*" not in r.reply
    assert r.reply.count(LOCATION_QUESTION["en"]) == 1


def test_first_aid_follows_reply_language(monkeypatch):
    _mock_json(monkeypatch, {"red_flag": False, "red_flag_category": "none", "reply_language": "es",
                             "reply": "Descansa el pie."})
    r = first_aid_reply("me torcí el tobillo", _profile("en"))
    assert r.reply.endswith(LOCATION_QUESTION["es"])


def test_red_flag_returns_fixed_text_not_gemini_words(monkeypatch):
    _mock_json(monkeypatch, {"red_flag": True, "red_flag_category": "stroke", "reply_language": "en",
                             "reply": "GEMINI WORDS"})
    r = first_aid_reply("his face is drooping", _profile())
    assert r.red_flag
    assert "GEMINI WORDS" not in r.reply
    assert "911" in r.reply
    assert "988" not in r.reply  # not a crisis category


@pytest.mark.parametrize("category", ["self_harm", "overdose", "other_emergency", "none", "made_up"])
def test_red_flag_crisis_or_unknown_category_includes_988(monkeypatch, category):
    _mock_json(monkeypatch, {"red_flag": True, "red_flag_category": category, "reply_language": "en", "reply": ""})
    r = first_aid_reply("...", _profile())
    assert "911" in r.reply and "988" in r.reply


def test_red_flag_spanish_uses_safety_module_text(monkeypatch):
    from app.safety.keywords import EMERGENCY_REPLY, SELF_HARM_REPLY
    _mock_json(monkeypatch, {"red_flag": True, "red_flag_category": "self_harm", "reply_language": "es", "reply": ""})
    assert first_aid_reply("quiero morir", _profile("es")).reply == SELF_HARM_REPLY["es"]
    _mock_json(monkeypatch, {"red_flag": True, "red_flag_category": "cardiac", "reply_language": "es", "reply": ""})
    assert first_aid_reply("me duele el pecho", _profile("es")).reply == EMERGENCY_REPLY["es"]


def test_first_aid_gemini_down_still_replies(monkeypatch):
    _mock_json(monkeypatch, exc=GeminiError("503"))
    r = first_aid_reply("ankle", _profile("es"))
    assert not r.red_flag
    assert "911" in r.reply
    assert r.reply.endswith(LOCATION_QUESTION["es"])


def test_prompt_contains_red_flags_and_no_diagnosis_rule(monkeypatch):
    calls = _mock_json(monkeypatch, {"red_flag": False, "red_flag_category": "none",
                                     "reply_language": "en", "reply": "x"})
    first_aid_reply("ankle", _profile())
    system = calls[0]["system"]
    assert "chest pain" in system and "suicide" in system
    assert "Never diagnose" in system


# ------------------------------------------------------------------ level_of_care (mocked)

def test_level_of_care_parses_and_clamps(monkeypatch):
    _mock_json(monkeypatch, {"level": "urgent_care", "confidence": 1.4})
    assert level_of_care("ankle", _profile()) == (CareLevel.URGENT_CARE, 1.0)


def test_level_of_care_bad_level_raises(monkeypatch):
    _mock_json(monkeypatch, {"level": "hospital", "confidence": 0.9})
    with pytest.raises(GeminiError):
        level_of_care("ankle", _profile())


# ------------------------------------------------------------------ gemini wrapper (mocked SDK)

class _Resp:
    def __init__(self, text):
        self.text = text
        self.candidates = []


class _FakeModels:
    def __init__(self, behaviours):
        self.behaviours = behaviours  # model name -> text or Exception
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        b = self.behaviours[model]
        if isinstance(b, Exception):
            raise b
        return _Resp(b)


class _FakeClient:
    def __init__(self, behaviours):
        self.models = _FakeModels(behaviours)


def _install(monkeypatch, behaviours, primary="primary-model"):
    client = _FakeClient(behaviours)
    monkeypatch.setattr(gemini, "_client", client)
    monkeypatch.setattr(gemini.settings, "gemini_model", primary)
    monkeypatch.setattr(gemini, "FALLBACK_MODEL", "fallback-model")
    return client


def test_generate_json_primary_ok(monkeypatch):
    c = _install(monkeypatch, {"primary-model": '{"a": 1}'})
    assert gemini.generate_json("p", {"type": "object"}) == {"a": 1}
    assert c.models.calls == ["primary-model"]


def test_generate_json_falls_back_to_second_model(monkeypatch):
    c = _install(monkeypatch, {"primary-model": RuntimeError("503 high demand"), "fallback-model": '{"a": 2}'})
    assert gemini.generate_json("p", {"type": "object"}) == {"a": 2}
    assert c.models.calls == ["primary-model", "fallback-model"]


def test_generate_json_both_fail_raises(monkeypatch):
    _install(monkeypatch, {"primary-model": RuntimeError("503"), "fallback-model": RuntimeError("timeout")})
    with pytest.raises(GeminiError):
        gemini.generate_json("p", {"type": "object"})


def test_generate_json_rejects_non_object(monkeypatch):
    _install(monkeypatch, {"primary-model": "[1, 2]", "fallback-model": "[1, 2]"})
    with pytest.raises(GeminiError):
        gemini.generate_json("p", {"type": "object"})


def test_same_primary_and_fallback_called_once(monkeypatch):
    c = _install(monkeypatch, {"fallback-model": RuntimeError("503")}, primary="fallback-model")
    with pytest.raises(GeminiError):
        gemini.generate_text("p")
    assert c.models.calls == ["fallback-model"]


def test_image_mime_sniffing():
    assert gemini._image_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert gemini._image_mime(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert gemini._image_mime(b"\x00\x00\x00\x18ftypheicrest") == "image/heic"
    assert gemini._image_mime(b"%PDF-1.7 rest") == "application/pdf"


# ------------------------------------------------------------------ live (real Gemini)

live = pytest.mark.skipif(os.getenv("RUN_LIVE_GEMINI") != "1", reason="set RUN_LIVE_GEMINI=1 to call Gemini")


@live
@pytest.mark.parametrize("lang,msg", [("en", "think i sprained my ankle"), ("es", "creo que me torcí el tobillo")])
def test_live_ankle(lang, msg):
    r = first_aid_reply(msg, _profile(lang))
    assert not r.red_flag
    assert r.reply.endswith(LOCATION_QUESTION[lang])
    assert "911" in r.reply
    level, conf = level_of_care(msg, _profile(lang))
    assert bump_if_unsure(level, conf) in (CareLevel.URGENT_CARE, CareLevel.ER)


@live
@pytest.mark.parametrize("msg", [
    "my dad's words are coming out weird and his smile looks uneven",
    "my 6 week old feels really hot",
])
def test_live_second_net_catches_red_flags(msg):
    assert first_aid_reply(msg, _profile()).red_flag
