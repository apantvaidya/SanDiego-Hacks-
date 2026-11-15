"""Tests for #10 onboarding with an in-memory store (#9) and faked Gemini / media download."""
import sys
import types
import uuid

import pytest

from app.conversation import onboarding
from app.conversation.onboarding import (CHG_PLAN_ID, KAISER_PLAN_ID, extract_card, guess_language, handle_onboarding,
                                         plan_from)
from app.llm.gemini import GeminiError
from app.models import CardExtraction, CoverageType, InboundMessage, Profile, User


class FakeStore:
    def __init__(self):
        self.convos, self.profiles, self.active, self.lang = {}, {}, {}, {}

    def get_conversation(self, user_id):
        return self.convos.get(user_id, ("new", {}))

    def set_conversation(self, user_id, state, context=None):
        old = self.convos.get(user_id, ("new", {}))[1]
        self.convos[user_id] = (state, old if context is None else context)

    def upsert_profile(self, user_id, label, **fields):
        p = Profile(id=uuid.uuid4(), user_id=user_id, label=label, coverage_type=CoverageType(fields["coverage_type"]),
                    insurer=fields.get("insurer"), plan_id=fields.get("plan_id"),
                    household_size=fields.get("household_size"), income_range_fpl=fields.get("income_range_fpl"),
                    language=fields.get("language", "en"))
        self.profiles[(user_id, label)] = (p, fields)
        return p

    def set_active_profile(self, user_id, profile_id):
        self.active[user_id] = profile_id

    def set_language(self, user_id, language):
        self.lang[user_id] = language


@pytest.fixture
def env(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(onboarding, "_store", lambda: store)
    downloads = []
    msg_mod = types.ModuleType("app.messaging")
    msg_mod.download_media = lambda url: downloads.append(url) or b"\xff\xd8\xffJPEGBYTES"
    monkeypatch.setitem(sys.modules, "app.messaging", msg_mod)
    return store, downloads


def user():
    return User(id=uuid.uuid4(), phone="+15550000000", active_profile_id=None)


def say(u, text="", media=None):
    return handle_onboarding(u, InboundMessage(phone=u.phone, text=text, media_urls=media or []))


def card(monkeypatch, extraction):
    monkeypatch.setattr(onboarding, "extract_card", lambda image: extraction)


def test_full_flow_card_photo_kaiser(env, monkeypatch):
    store, downloads = env
    card(monkeypatch, CardExtraction("Kaiser Permanente", "Silver 70 HMO", CoverageType.KAISER, 0.95))
    u = user()
    welcome = say(u, "hi")[0]
    assert "not for emergencies" in welcome and "911" in welcome and "NONE, MEDI-CAL, or OTHER" in welcome
    assert store.convos[u.id][0] == "onboarding_card"

    assert say(u, "", ["https://cdn.example/card.jpg"]) == ["Looks like Kaiser Permanente. Right? (Y/N)"]
    assert downloads == ["https://cdn.example/card.jpg"]
    assert say(u, "yes") == ["How many people are in your household, including you?"]

    menu = say(u, "3")[0]
    assert "1. Under $37,702" in menu and "2. $37,702 to $54,640" in menu and "4. Over $109,280" in menu
    assert "5. Skip" in menu and "This unlocks discounts at clinics." in menu

    assert "English or Español?" in say(u, "2")[0]
    done = say(u, "1")[0]
    assert "Kaiser Permanente" in done and "never your card or member ID" in done

    profile, fields = store.profiles[(u.id, "me")]
    assert fields == {"coverage_type": "kaiser", "insurer": "Kaiser Permanente", "plan_id": KAISER_PLAN_ID,
                      "household_size": 3, "income_range_fpl": "138-200", "language": "en"}
    assert store.active[u.id] == profile.id and store.lang[u.id] == "en"
    assert store.convos[u.id] == ("idle", {})  # context cleared


def test_context_never_holds_image_or_member_id(env, monkeypatch):
    store, _ = env
    card(monkeypatch, CardExtraction("Blue Shield of California", "PPO", CoverageType.COMMERCIAL, 0.9))
    u = user()
    say(u, "hi")
    say(u, "", ["https://cdn.example/card.jpg"])
    ctx = store.convos[u.id][1]
    flat = repr(ctx)
    assert "JPEGBYTES" not in flat and "member" not in flat.lower()
    assert set(ctx["onb"]) <= {"lang", "coverage_type", "insurer", "plan_id", "plan_name"}


def test_none_goes_to_household_as_uninsured(env):
    store, _ = env
    u = user()
    say(u, "hello")
    assert say(u, "NONE") == ["How many people are in your household, including you?"]
    assert store.convos[u.id][1]["onb"]["coverage_type"] == "uninsured"


def test_medi_cal_asks_plan_then_chg(env):
    store, _ = env
    u = user()
    say(u, "hi")
    assert "Community Health Group" in say(u, "MEDI-CAL")[0]
    say(u, "1")
    onb = store.convos[u.id][1]["onb"]
    assert (onb["coverage_type"], onb["plan_id"]) == ("medi_cal", CHG_PLAN_ID)
    assert store.convos[u.id][0] == "onboarding_household"


def test_medi_cal_other_plan_has_no_plan_id(env):
    store, _ = env
    u = user()
    say(u, "hi")
    say(u, "medi-cal")
    say(u, "2")
    assert store.convos[u.id][1]["onb"]["plan_id"] is None


def test_unreadable_card_stays_and_explains(env, monkeypatch):
    store, _ = env
    card(monkeypatch, CardExtraction(None, None, None, 0.2))
    u = user()
    say(u, "hi")
    assert "couldn't read that card" in say(u, "", ["https://cdn.example/blurry.jpg"])[0]
    assert store.convos[u.id][0] == "onboarding_card"


def test_no_then_type_name(env, monkeypatch):
    store, _ = env
    card(monkeypatch, CardExtraction("Aetna", "Choice POS II", CoverageType.COMMERCIAL, 0.9))
    u = user()
    say(u, "hi")
    say(u, "", ["https://cdn.example/card.jpg"])
    assert "Type your insurance name" in say(u, "no")[0]
    assert say(u, "Community Health Group") == ["Looks like Community Health Group (Medi-Cal). Right? (Y/N)"]


def test_spanish_flow(env):
    store, _ = env
    u = user()
    welcome = say(u, "hola necesito ayuda")[0]
    assert welcome.startswith("Hola, soy Care Navigator") and "no es para emergencias" in welcome
    assert "¿Cuántas personas" in say(u, "ninguno")[0]
    assert say(u, "dos")[0].startswith("¿Cuál es el ingreso anual")
    say(u, "5")
    assert say(u, "2")[0].startswith("Listo")
    _, fields = store.profiles[(u.id, "me")]
    assert fields["language"] == "es" and fields["income_range_fpl"] is None and fields["household_size"] == 2


def test_bad_inputs_reprompt(env):
    store, _ = env
    u = user()
    say(u, "hi")
    say(u, "none")
    assert say(u, "lots") == ["Please reply with a number, like 1 or 4."]
    say(u, "4")
    assert "1. Under $45,540" in say(u, "banana")[0]
    say(u, "1")
    assert "English or Español?" in say(u, "french")[0]


@pytest.mark.parametrize("insurer,plan,cov,expected", [
    ("Kaiser Permanente", None, None, ("kaiser", KAISER_PLAN_ID)),
    ("Community Health Group", "Medi-Cal", CoverageType.MEDI_CAL, ("medi_cal", CHG_PLAN_ID)),
    ("Blue Shield Promise", "Medi-Cal", CoverageType.MEDI_CAL, ("medi_cal", None)),
    ("Medicare", None, None, ("medicare", None)),
    ("Anthem Blue Cross", "Covered California Silver", None, ("covered_ca", None)),
    ("Aetna", "PPO", CoverageType.COMMERCIAL, ("commercial", None)),
])
def test_plan_from(insurer, plan, cov, expected):
    out = plan_from(insurer, plan, cov)
    assert (out["coverage_type"], out["plan_id"]) == expected


def test_extract_card_schema_has_no_member_id(monkeypatch):
    seen = {}

    def fake(prompt, schema, image=None, system=None):
        seen.update(schema=schema, system=system, image=image)
        return {"insurer": "Kaiser Permanente", "plan_name": "HMO", "coverage_type": "kaiser", "confidence": 1.3}

    monkeypatch.setattr(onboarding, "generate_json", fake)
    c = extract_card(b"img")
    assert c == CardExtraction("Kaiser Permanente", "HMO", CoverageType.KAISER, 1.0)
    assert not any("member" in k or "id" == k for k in seen["schema"]["properties"])
    assert "Do NOT read or return member ID" in seen["system"]


def test_extract_card_gemini_down(monkeypatch):
    monkeypatch.setattr(onboarding, "generate_json", lambda **k: (_ for _ in ()).throw(GeminiError("503")))
    assert extract_card(b"img").confidence == 0.0


def test_guess_language():
    assert guess_language("Hola, me duele el tobillo") == "es"
    assert guess_language("hi there") == "en"
