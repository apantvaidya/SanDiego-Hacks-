import pytest

from app.safety.keywords import (
    EMERGENCY_REPLY,
    SELF_HARM_REPLY,
    SELF_HARM_WITH_EMERGENCY,
    normalize,
    screen,
)

POSITIVES = [
    # cardiac
    ("I have chest pain", "cardiac"),
    ("chest pian since this morning", "cardiac"),
    ("my chest feels tight and heavy", "cardiac"),
    ("there's a lot of pressure in my chest", "cardiac"),
    ("I think my dad is having a heart attack", "cardiac"),
    ("CHEST HURTS", "cardiac"),
    ("tengo dolor de pecho", "cardiac"),
    ("me duele el pecho mucho", "cardiac"),
    # breathing
    ("i cant breathe", "breathing"),
    ("I can't breath", "breathing"),
    ("having trouble breathing", "breathing"),
    ("shortness of breath when I walk", "breathing"),
    ("my son is choking", "breathing"),
    ("her lips are blue", "breathing"),
    ("no puedo respirar", "breathing"),
    ("tiene dificultad para respirar", "breathing"),
    # stroke
    ("I think she's having a stroke", "stroke"),
    ("his face is drooping on one side", "stroke"),
    ("my mom's speech is slurred", "stroke"),
    ("sudden numbness in my arm", "stroke"),
    ("worst headache of my life", "stroke"),
    ("creo que es un derrame cerebral", "stroke"),
    ("tiene la cara caida", "stroke"),
    # bleeding
    ("I'm bleeding a lot from my leg", "bleeding"),
    ("the cut is bleeding and it won't stop", "bleeding"),
    ("I can't stop the bleeding", "bleeding"),
    ("coughing up blood", "bleeding"),
    ("no para de sangrar", "bleeding"),
    # overdose / poisoning
    ("I think I overdosed", "overdose"),
    ("my kid swallowed bleach", "overdose"),
    ("he took too many pills", "overdose"),
    ("tomo muchas pastillas", "overdose"),
    # unconscious / seizure
    ("my grandpa passed out and won't wake up", "unconscious"),
    ("she is having a seizure", "unconscious"),
    ("he's unresponsive", "unconscious"),
    ("mi abuela esta inconsciente", "unconscious"),
    # allergy
    ("my throat is closing up after eating peanuts", "severe_allergy"),
    ("his tongue is swelling", "severe_allergy"),
    ("se me cierra la garganta", "severe_allergy"),
    # head injury
    ("I hit my head and now I'm confused and threw up", "head_injury"),
    ("he got knocked out playing football", "head_injury"),
    # pregnancy (CDC Hear Her)
    ("I'm 20 weeks pregnant and bleeding", "pregnancy"),
    ("estoy embarazada y estoy sangrando", "pregnancy"),
    # infant fever with stated age under 3 months
    ("my 3 week old has a fever of 100.6", "infant_fever"),
    ("my newborn feels hot, fever 101", "infant_fever"),
    ("mi bebe de 2 meses tiene fiebre", "infant_fever"),
]

SELF_HARM = [
    "I want to kill myself",
    "i've been thinking about suicide",
    "I just want to die",
    "I don't want to live anymore",
    "thinking about hurting myself",
    "im sucidal",
    "quiero morir",
    "estoy pensando en suicidarme",
    "I'm not suicidal but I think about it",  # self-harm ignores negation on purpose
]

NEGATIVES = [
    "think i sprained my ankle",
    "I need a routine blood test",
    "where can I get a flu shot",
    "my kid has a runny nose",
    "I think I have food poisoning",
    "I got poison ivy on my arm",
    "no chest pain, just my ankle",
    "I don't have chest pain, but my knee is swollen",
    "my chest workout was hard yesterday",
    "can I get a physical for school",
    "me torci el tobillo",
    "necesito un examen de sangre",
    "my baby has a cold",
    "",
    "#profile kaiser",
    "92103",
]


@pytest.mark.parametrize("text,category", POSITIVES)
def test_red_flags(text, category):
    result = screen(text)
    assert result.matched, text
    assert result.category == category, (text, result.category)
    assert "911" in result.reply


@pytest.mark.parametrize("text", SELF_HARM)
def test_self_harm_gets_988(text):
    result = screen(text)
    assert result.matched, text
    assert result.category == "self_harm"
    assert "988" in result.reply


@pytest.mark.parametrize("text", NEGATIVES)
def test_negatives(text):
    assert not screen(text).matched, text


def test_self_harm_plus_overdose_gets_911_and_988():
    result = screen("I took too many pills because I want to die")
    assert result.reply == SELF_HARM_WITH_EMERGENCY["en"]
    assert "911" in result.reply and "988" in result.reply


def test_negation_does_not_hide_a_later_red_flag():
    assert screen("no, I have chest pain").matched
    assert screen("no chest pain but I can't breathe").category == "breathing"


def test_spanish_reply():
    assert screen("dolor de pecho").reply == EMERGENCY_REPLY["es"]
    assert screen("quiero morir").reply == SELF_HARM_REPLY["es"]
    assert screen("chest pain", language="es").reply == EMERGENCY_REPLY["es"]


def test_english_reply_by_default():
    assert screen("chest pain").reply == EMERGENCY_REPLY["en"]


def test_normalize():
    assert normalize("Can’t  BREATHE") == "cant breathe"
    assert normalize("Presión en el pecho") == "presion en el pecho"
