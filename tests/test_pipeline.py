"""Tests for #17 pipeline with every dependency faked (no network, no DB).
The conversation store (#9) is replaced by an in-memory fake."""
import sys
import time
import types
import uuid
from datetime import datetime, timezone

import pytest

from app import pipeline
from app.followup.scheduler import followup_context
from app.llm.gemini import GeminiError
from app.models import (CareLevel, CostEstimate, CoverageType, GeoPoint, InboundMessage, Location, Profile,
                        RankedOption, TriageResult, Verification)


class FakeStore:
    def __init__(self):
        self.convos = {}

    def get_conversation(self, user_id):
        return self.convos.get(user_id, ("new", {}))

    def set_conversation(self, user_id, state, context=None):
        old = self.convos.get(user_id, ("new", {}))[1]
        self.convos[user_id] = (state, old if context is None else context)


def loc(id, kind="urgent_care", miles=2.0):
    return Location(id=id, name=id, kind=kind, lat=32.8, lon=-117.2, address="a", phone=None, hours={},
                    has_xray=True, walk_in=True, accepts=["uninsured"], self_pay_price=199, sliding_scale=None,
                    source_url="u", crawled_at=datetime(2026, 11, 15, tzinfo=timezone.utc), distance_miles=miles)


@pytest.fixture
def env(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(pipeline, "_store", lambda: store)
    calls = {"level": 0, "find": [], "verify": 0, "followup": []}

    def fake_level(text, profile):
        calls["level"] += 1
        return CareLevel.URGENT_CARE, 0.9

    monkeypatch.setattr(pipeline, "level_of_care", fake_level)
    monkeypatch.setattr(pipeline, "first_aid_reply",
                        lambda text, p: TriageResult(False, "Rest and ice.\n\nWhere are you right now?"))
    monkeypatch.setattr(pipeline, "extract_place", lambda text: None if "home" in text else text)
    monkeypatch.setattr(pipeline, "geocode", lambda place: GeoPoint(32.88, -117.23, place))
    cands = [loc("a", miles=1.0), loc("b", "fqhc", 3.0), loc("c", miles=4.0)]

    def fake_find(profile, level, point, radius_miles=5, limit=10, *, needs_xray=False, at=None):
        calls["find"].append((level, needs_xray))
        return (cands if profile.label != "nothing" else []), 15.0

    monkeypatch.setattr(pipeline, "find_candidates_widening", fake_find)
    monkeypatch.setattr(pipeline, "rank", lambda c, p, s, l: [RankedOption("b", "cheap"), RankedOption("a", "x-ray")])
    monkeypatch.setattr(pipeline, "estimate_cost", lambda l, p, lv: CostEstimate(199, 199, "self_pay"))
    monkeypatch.setattr(pipeline, "render_options",
                        lambda opts, lang: "|".join(f"{o.location.id}:{bool(o.verification)}" for o in opts))
    monkeypatch.setattr(pipeline, "render_fallback", lambda p, lang: "FALLBACK")

    def fake_verify(location):
        calls["verify"] += 1
        return Verification(location.id, datetime.now(timezone.utc), True)

    verify_mod = types.ModuleType("app.care.verify")
    verify_mod.verify_live = fake_verify
    monkeypatch.setitem(sys.modules, "app.care.verify", verify_mod)
    sched = types.ModuleType("app.followup.scheduler")
    sched.schedule_followup = lambda uid, topic, hours=20: calls["followup"].append(topic)
    sched.followup_context = followup_context
    monkeypatch.setitem(sys.modules, "app.followup.scheduler", sched)
    return store, calls


def prof(label="uninsured", lang="en"):
    return Profile(id=uuid.uuid4(), user_id=uuid.uuid4(), label=label, coverage_type=CoverageType.UNINSURED,
                   language=lang)


def msg(text):
    return InboundMessage(phone="+15550000000", text=text)


def test_first_reply_sets_awaiting_location_with_level(env):
    store, calls = env
    p = prof()
    assert pipeline.first_reply(msg("think i sprained my ankle"), p) == ["Rest and ice.\n\nWhere are you right now?"]
    state, ctx = store.get_conversation(p.user_id)
    assert state == "awaiting_location"
    assert ctx == {"symptom_text": "think i sprained my ankle", "level_of_care": "urgent_care", "level_confidence": 0.9}


def test_first_reply_red_flag_goes_idle(env, monkeypatch):
    store, _ = env
    monkeypatch.setattr(pipeline, "first_aid_reply", lambda text, p: TriageResult(True, "Call 911"))
    p = prof()
    assert pipeline.first_reply(msg("his face is drooping"), p) == ["Call 911"]
    assert store.get_conversation(p.user_id) == ("idle", {})


def test_options_reply_full_flow(env):
    store, calls = env
    p = prof()
    pipeline.first_reply(msg("think i sprained my ankle"), p)
    out = pipeline.options_reply(msg("La Jolla"), p, "think i sprained my ankle")
    assert out[0].split("\n\n")[0] == "b:True|a:False"  # ranked order, only #1 verified
    assert "CARD" in out[0]
    assert calls["level"] == 1        # reused from Text 1, not recomputed
    assert calls["find"] == [(CareLevel.URGENT_CARE, True)]  # ankle -> needs X-ray
    state, ctx = store.get_conversation(p.user_id)
    assert state == "idle"
    answered_at = ctx.pop("answered_at")
    assert pipeline.is_recent({**ctx, "answered_at": answered_at})
    assert ctx == {"symptom_text": "think i sprained my ankle", "last_level_of_care": "urgent_care",
                   "recommended_location_ids": ["b", "a"],
                   "point": {"lat": 32.88, "lon": -117.23, "label": "La Jolla"}}
    assert calls["followup"] == ["think i sprained my ankle"]


def test_options_reply_unknown_place_asks_for_zip(env):
    store, _ = env
    p = prof(lang="es")
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "tobillo"})
    out = pipeline.options_reply(msg("en mi casa, at home"), p, "tobillo")
    assert "código postal" in out[0]
    assert store.get_conversation(p.user_id) == ("awaiting_location", {"symptom_text": "tobillo"})


def test_options_reply_no_candidates_sends_fallback(env):
    store, _ = env
    p = prof(label="nothing")
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "ankle", "level_of_care": "urgent_care",
                                                             "level_confidence": 0.9})
    assert pipeline.options_reply(msg("La Jolla"), p, "ankle") == ["FALLBACK"]
    assert store.get_conversation(p.user_id)[0] == "idle"


def test_low_confidence_bumps_level(env):
    store, calls = env
    p = prof()
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "x", "level_of_care": "urgent_care",
                                                             "level_confidence": 0.4})
    pipeline.options_reply(msg("La Jolla"), p, "x")
    assert calls["find"][-1][0] is CareLevel.ER


def test_self_care_shows_a_clinic(env):
    store, calls = env
    p = prof()
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "sore throat",
                                                             "level_of_care": "self_care", "level_confidence": 0.95})
    pipeline.options_reply(msg("La Jolla"), p, "sore throat")
    assert calls["find"][-1] == (CareLevel.PRIMARY_CARE, False)


def test_level_computed_when_missing_and_gemini_down(env, monkeypatch):
    store, calls = env
    monkeypatch.setattr(pipeline, "level_of_care", lambda t, p: (_ for _ in ()).throw(GeminiError("503")))
    p = prof()
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "ankle"})
    pipeline.options_reply(msg("La Jolla"), p, "ankle")
    assert calls["find"][-1][0] is CareLevel.ER  # urgent care at zero confidence, bumped


def test_slow_verify_is_skipped(env, monkeypatch):
    store, calls = env
    monkeypatch.setattr(pipeline, "VERIFY_BUDGET_S", 0.05)

    def slow(location):
        time.sleep(0.5)
        return Verification(location.id, datetime.now(timezone.utc), True)

    sys.modules["app.care.verify"].verify_live = slow
    p = prof()
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "ankle", "level_of_care": "urgent_care",
                                                             "level_confidence": 0.9})
    assert pipeline.options_reply(msg("La Jolla"), p, "ankle")[0].startswith("b:False|a:False\n\n")


def test_verify_not_implemented_is_skipped(env):
    store, _ = env

    def stub(location):
        raise NotImplementedError

    sys.modules["app.care.verify"].verify_live = stub
    p = prof()
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "ankle", "level_of_care": "urgent_care",
                                                             "level_confidence": 0.9})
    assert pipeline.options_reply(msg("La Jolla"), p, "ankle")[0].startswith("b:False|a:False\n\n")


@pytest.mark.parametrize("text,expected", [
    ("think i sprained my ankle", True), ("creo que me torcí el tobillo", True), ("fell off my bike", True),
    ("i have a sore throat", False), ("fever and chills", False),
])
def test_needs_xray(text, expected):
    assert pipeline.needs_xray(text) is expected


def test_red_flag_keeps_the_last_answer_for_followup(env, monkeypatch):
    store, _ = env
    p = prof()
    done = {"symptom_text": "ankle", "last_level_of_care": "urgent_care", "recommended_location_ids": ["a"]}
    store.set_conversation(p.user_id, "idle", done)
    monkeypatch.setattr(pipeline, "first_aid_reply", lambda text, p: TriageResult(True, "Call 911"))
    assert pipeline.first_reply(msg("his face is drooping"), p) == ["Call 911"]
    assert store.get_conversation(p.user_id) == ("idle", done)



# --------------------------------------------------------------------------- after_answer

def _answered(env, p):
    store, calls = env
    pipeline.first_reply(msg("think i sprained my ankle"), p)
    pipeline.options_reply(msg("UCSD"), p, "think i sprained my ankle")
    return store.get_conversation(p.user_id)[1]


def test_no_place_named_gets_friendly_reask(env, monkeypatch):
    store, _ = env
    monkeypatch.setattr(pipeline, "extract_place", lambda text: None)
    p = prof()
    store.set_conversation(p.user_id, "awaiting_location", {"symptom_text": "ankle"})
    out = pipeline.options_reply(msg("i don't know how to get there"), p, "ankle")
    assert "cross street" in out[0] and "couldn't find" not in out[0]
    assert store.get_conversation(p.user_id)[0] == "awaiting_location"


def test_is_recent():
    now = datetime(2026, 11, 15, 19, 0, tzinfo=timezone.utc)
    ctx = {"recommended_location_ids": ["a"], "answered_at": "2026-11-15T18:00:00+00:00"}
    assert pipeline.is_recent(ctx, now)
    assert not pipeline.is_recent({**ctx, "answered_at": "2026-11-14T18:00:00+00:00"}, now)
    assert not pipeline.is_recent({"answered_at": "2026-11-15T18:00:00+00:00"}, now)
    assert not pipeline.is_recent({**ctx, "answered_at": "garbage"}, now)


def test_update_reanswers_at_saved_place_without_asking(env, monkeypatch):
    store, calls = env
    p = prof()
    ctx = _answered(env, p)
    monkeypatch.setattr(pipeline, "_sort", lambda prev, text: "update")
    seen = {}
    monkeypatch.setattr(pipeline, "first_aid_reply",
                        lambda text, pr: seen.setdefault("t", text) and TriageResult(False, "Keep it still.\n\nWhere are you right now?"))
    out = pipeline.after_answer(msg("it hurts a lot it might be broken"), p, ctx)
    assert out[0] == "Keep it still."           # no "Where are you" again
    assert out[1].startswith("b:True|a:False")   # options at the saved point
    assert seen["t"] == "think i sprained my ankle. Then: it hurts a lot it might be broken"
    assert store.get_conversation(p.user_id)[0] == "idle"


def test_update_never_steps_down_a_level(env, monkeypatch):
    store, calls = env
    p = prof()
    ctx = {**_answered(env, p), "last_level_of_care": "er"}
    monkeypatch.setattr(pipeline, "_sort", lambda prev, text: "update")
    calls["find"].clear()
    pipeline.after_answer(msg("it's a bit better actually but still swollen"), p, ctx)
    assert calls["find"][0][0] == CareLevel.ER


def test_update_red_flag_returns_emergency(env, monkeypatch):
    store, _ = env
    p = prof()
    ctx = _answered(env, p)
    monkeypatch.setattr(pipeline, "_sort", lambda prev, text: "update")
    monkeypatch.setattr(pipeline, "first_aid_reply", lambda text, pr: TriageResult(True, "Call 911"))
    assert pipeline.after_answer(msg("bone is sticking out"), p, ctx) == ["Call 911"]


def test_directions_from_database(env, monkeypatch):
    p = prof()
    ctx = _answered(env, p)
    monkeypatch.setattr(pipeline, "_sort", lambda prev, text: "directions")
    monkeypatch.setattr(pipeline, "_locations_by_id", lambda ids: [
        {"id": "b", "name": "Clinic B", "address": "4085 Governor Dr, San Diego, CA 92122"}])
    out = pipeline.after_answer(msg("how do i get there"), p, ctx)
    assert "Clinic B" in out[0]
    assert "https://maps.apple.com/?daddr=4085+Governor+Dr%2C+San+Diego%2C+CA+92122" in out[0]


def test_new_question_and_other(env, monkeypatch):
    p = prof()
    ctx = _answered(env, p)
    monkeypatch.setattr(pipeline, "_sort", lambda prev, text: "new")
    assert pipeline.after_answer(msg("my kid has a fever"), p, ctx) is None
    monkeypatch.setattr(pipeline, "_sort", lambda prev, text: "other")
    assert "DIRECTIONS" in pipeline.after_answer(msg("thanks"), p, ctx)[0]


def test_sort_falls_back_to_update_when_gemini_fails(monkeypatch):
    monkeypatch.setattr(pipeline, "generate_json", lambda **kw: (_ for _ in ()).throw(GeminiError("503")))
    assert pipeline._sort("ankle", "it hurts more") == "update"



def test_card_words_skip_gemini(monkeypatch):
    monkeypatch.setattr(pipeline, "generate_json", lambda **kw: (_ for _ in ()).throw(AssertionError("no Gemini")))
    assert pipeline._sort("ankle", "CARD") == "card"
    assert pipeline._sort("tobillo", "tarjeta") == "card"
    assert pipeline._sort("ankle", "directions") == "directions"


def test_front_desk_card_english(env, monkeypatch):
    p = prof()
    ctx = _answered(env, p)
    monkeypatch.setattr(pipeline, "_locations_by_id", lambda ids: [{"id": "b", "name": "UCSD Urgent Care", "address": "x"}])
    out = pipeline.after_answer(msg("card"), p, ctx)[0]
    assert "FRONT DESK CARD" in out
    assert "Reason for visit: think i sprained my ankle" in out
    assert "No insurance, self-pay" in out
    assert "Here for: UCSD Urgent Care" in out
    assert "Spanish" not in out


def test_front_desk_card_spanish_translates_and_asks_for_interpreter(env, monkeypatch):
    p = prof(lang="es")
    ctx = {**_answered(env, p), "symptom_text": "creo que me torcí el tobillo"}
    monkeypatch.setattr(pipeline, "_locations_by_id", lambda ids: [])
    monkeypatch.setattr(pipeline, "generate_json", lambda **kw: {"english": "I think I sprained my ankle"})
    out = pipeline.after_answer(msg("tarjeta"), p, ctx)[0]
    assert out.startswith("Muestra esto")
    assert "Reason for visit: I think I sprained my ankle" in out
    assert "Please offer a Spanish interpreter" in out
