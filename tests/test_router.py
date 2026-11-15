"""Router tests against the shared Neon database. Each test uses a fake 555 number and
deletes its user afterward (cascades to profiles, conversations, follow-ups)."""
import uuid

import pytest

from app import pipeline
from app.config import settings
from app.conversation import router, store
from app.models import InboundMessage

pytestmark = pytest.mark.skipif(not settings.database_url, reason="DATABASE_URL not set")


@pytest.fixture
def phone():
    number = "+1555" + str(uuid.uuid4().int)[:7]
    yield number
    user = store.get_or_create_user(number)
    store.delete_user(user.id)


def send(phone, text="", media=None):
    return router.handle(InboundMessage(phone=phone, text=text, media_urls=media or []))


def test_new_user_without_onboarding_gets_demo_instructions(phone):
    replies = send(phone, "hi")
    assert len(replies) == 1 and "#profile" in replies[0]


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


def test_question_flow_with_placeholder_pipeline(phone):
    send(phone, "#profile kaiser")
    first = send(phone, "think i sprained my ankle")[0]
    assert "Where are you" in first
    user = store.get_or_create_user(phone)
    state, context = store.get_conversation(user.id)
    assert state == "awaiting_location" and context["symptom_text"] == "think i sprained my ankle"
    second = send(phone, "La Jolla")[0]
    assert "still being built" in second
    assert store.get_conversation(user.id)[0] == "idle"


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
    assert "Dónde" in send(phone, "me torcí el tobillo")[0]


def test_reset_and_reset_all(phone):
    send(phone, "#profile kaiser")
    send(phone, "think i sprained my ankle")
    send(phone, "#reset")
    user = store.get_or_create_user(phone)
    assert store.get_conversation(user.id) == ("idle", {})
    send(phone, "#reset all")
    assert store.list_profiles(user.id) == []
    assert "#profile" in send(phone, "hello")[0]


def test_followup_placeholder(phone):
    send(phone, "#profile kaiser")
    assert "aren't ready" in send(phone, "#followup")[0]


def test_photo_without_text_goes_to_onboarding(phone):
    replies = send(phone, "", media=["https://cdn.example.com/card.jpg"])
    assert "#profile" in replies[0]


def test_commands_without_hash(phone):
    assert "kaiser" in send(phone, "profile kaiser")[0]
    assert send(phone, "who am i")[0].startswith("Active profile: kaiser")
    assert "uninsured" in send(phone, "Profile uninsured")[0]
    assert send(phone, "whoami")[0].startswith("Active profile: uninsured")


def test_bare_words_that_are_questions_stay_questions(phone):
    send(phone, "#profile kaiser")
    assert "Where are you" in send(phone, "is there a language barrier at the clinic")[0]
    send(phone, "#reset")
    assert "Where are you" in send(phone, "profile of a good urgent care?")[0]


def test_profile_reply_uses_plain_names(phone):
    reply = send(phone, "#profile medi_cal")[0]
    assert "Community Health Group" in reply and "chg_medi_cal" not in reply
