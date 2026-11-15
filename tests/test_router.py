"""Router tests against the shared Neon database. Each test uses a fake 555 number and
deletes its user afterward (cascades to profiles, conversations, follow-ups)."""
import uuid

import pytest

from app import pipeline
from app.config import settings
from app.conversation import router, store
from app.models import InboundMessage

pytestmark = pytest.mark.skipif(not settings.database_url, reason="DATABASE_URL not set")


@pytest.fixture(autouse=True)
def fake_downstream(monkeypatch):
    """Test the router alone: fake onboarding (#10) and pipeline (#17), which call Gemini."""
    from app.conversation import onboarding

    def fake_onboarding(user, msg):
        return ["ONBOARDING"]

    def fake_first(msg, profile):
        user = store.get_or_create_user(msg.phone)
        store.set_conversation(user.id, "awaiting_location", {"symptom_text": msg.text})
        return [f"FIRST:{profile.language}:{profile.label}"]

    def fake_options(msg, profile, symptom_text):
        user = store.get_or_create_user(msg.phone)
        store.set_conversation(user.id, "idle", {})
        return [f"OPTIONS:{symptom_text}@{msg.text}"]

    monkeypatch.setattr(onboarding, "handle_onboarding", fake_onboarding)
    monkeypatch.setattr(pipeline, "first_reply", fake_first)
    monkeypatch.setattr(pipeline, "options_reply", fake_options)


@pytest.fixture
def phone():
    number = "+1555" + str(uuid.uuid4().int)[:7]
    yield number
    user = store.get_or_create_user(number)
    store.delete_user(user.id)


def send(phone, text="", media=None):
    return router.handle(InboundMessage(phone=phone, text=text, media_urls=media or []))


def test_new_user_goes_to_onboarding(phone):
    assert send(phone, "hi") == ["ONBOARDING"]


def test_placeholders_while_modules_are_stubs(phone, monkeypatch):
    from app.conversation import onboarding

    def stub(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(onboarding, "handle_onboarding", stub)
    monkeypatch.setattr(pipeline, "first_reply", stub)
    monkeypatch.setattr(pipeline, "options_reply", stub)
    assert "#profile" in send(phone, "hi")[0]
    send(phone, "#profile kaiser")
    assert "Where are you" in send(phone, "think i sprained my ankle")[0]
    user = store.get_or_create_user(phone)
    assert store.get_conversation(user.id) == ("awaiting_location", {"symptom_text": "think i sprained my ankle"})
    assert "still being built" in send(phone, "La Jolla")[0]
    assert store.get_conversation(user.id)[0] == "idle"


def test_profile_switch_and_whoami(phone):
    assert "kaiser" in send(phone, "#profile kaiser")[0]
    assert "kaiser_cc_silver" in send(phone, "#whoami")[0]
    assert "medi_cal" in send(phone, "#profile medi-cal")[0]
    assert "chg_medi_cal" in send(phone, "#whoami")[0]
    send(phone, "#profile uninsured")
    reply = send(phone, "#whoami")[0]
    assert "uninsured" in reply and "<138" in reply
    listing = send(phone, "#profile")[0]
    assert "kaiser" in listing and "medi_cal" in listing and "Active: uninsured" in listing


def test_unknown_profile(phone):
    assert "don't know" in send(phone, "#profile aetna")[0]


def test_question_flow(phone):
    send(phone, "#profile kaiser")
    assert send(phone, "think i sprained my ankle") == ["FIRST:en:kaiser"]
    assert send(phone, "La Jolla") == ["OPTIONS:think i sprained my ankle@La Jolla"]


def test_router_calls_pipeline_with_saved_symptom(phone, monkeypatch):
    seen = {}

    def first_reply(msg, profile):
        user = store.get_or_create_user(msg.phone)
        store.set_conversation(user.id, "awaiting_location", {"symptom_text": msg.text})
        return ["first"]

    def options_reply(msg, profile, symptom_text):
        seen.update(location=msg.text, symptom=symptom_text, plan=profile.plan_id)
        return ["options"]

    monkeypatch.setattr(pipeline, "first_reply", first_reply)
    monkeypatch.setattr(pipeline, "options_reply", options_reply)
    send(phone, "#profile medi_cal")
    assert send(phone, "my ankle hurts") == ["first"]
    assert send(phone, "92103") == ["options"]
    assert seen == {"location": "92103", "symptom": "my ankle hurts", "plan": "chg_medi_cal"}


def test_safety_wins_in_any_state(phone):
    send(phone, "#profile uninsured")
    send(phone, "think i sprained my ankle")  # now awaiting_location
    reply = send(phone, "wait now I have chest pain")[0]
    assert "911" in reply
    user = store.get_or_create_user(phone)
    assert store.get_conversation(user.id)[0] == "idle"


def test_safety_before_onboarding(phone):
    assert "988" in send(phone, "i want to kill myself")[0]


def test_stop_silences_until_start(phone):
    send(phone, "#profile kaiser")
    assert "unsubscribed" in send(phone, "STOP")[0]
    assert send(phone, "think i sprained my ankle") == []
    assert send(phone, "chest pain") == []  # opted out means no messages at all
    assert "Welcome back" in send(phone, "start")[0]
    assert send(phone, "#whoami")[0].startswith("Active profile: kaiser")


def test_help_includes_emergency_notice(phone):
    reply = send(phone, "HELP")[0]
    assert "911" in reply and "988" in reply and "STOP" in reply


def test_lang_switch(phone):
    send(phone, "#profile kaiser")
    assert "español" in send(phone, "#lang es")[0]
    assert send(phone, "#whoami")[0].startswith("Perfil activo")
    assert send(phone, "me torcí el tobillo") == ["FIRST:es:kaiser"]


def test_reset_and_reset_all(phone):
    send(phone, "#profile kaiser")
    send(phone, "think i sprained my ankle")
    send(phone, "#reset")
    user = store.get_or_create_user(phone)
    assert store.get_conversation(user.id) == ("idle", {})
    send(phone, "#reset all")
    assert store.list_profiles(user.id) == []
    assert send(phone, "hello") == ["ONBOARDING"]


def test_followup_placeholder(phone):
    send(phone, "#profile kaiser")
    assert "aren't ready" in send(phone, "#followup")[0]


def test_photo_without_text_goes_to_onboarding(phone):
    assert send(phone, "", media=["https://cdn.example.com/card.jpg"]) == ["ONBOARDING"]


def test_commands_without_hash(phone):
    assert "kaiser" in send(phone, "profile kaiser")[0]
    assert send(phone, "who am i")[0].startswith("Active profile: kaiser")
    assert "uninsured" in send(phone, "Profile uninsured")[0]
    assert send(phone, "whoami")[0].startswith("Active profile: uninsured")


def test_bare_words_that_are_questions_stay_questions(phone):
    send(phone, "#profile kaiser")
    assert send(phone, "is there a language barrier at the clinic") == ["FIRST:en:kaiser"]
    send(phone, "#reset")
    assert send(phone, "profile of a good urgent care?") == ["FIRST:en:kaiser"]


def test_profile_reply_uses_plain_names(phone):
    reply = send(phone, "#profile medi_cal")[0]
    assert "Community Health Group" in reply and "chg_medi_cal" not in reply
