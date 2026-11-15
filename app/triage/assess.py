"""First-aid reply and level of care. Never diagnose. Owned by #4.

The keyword screen (app/safety, #3) runs before anything here. These Gemini prompts repeat
the red flags as a second net only.
"""
from __future__ import annotations

import logging
import re

from app.llm.gemini import GeminiError, generate_json
from app.models import LEVEL_ORDER, CareLevel, Profile, TriageResult
from app.safety.keywords import EMERGENCY_REPLY, SELF_HARM_REPLY, SELF_HARM_WITH_EMERGENCY

log = logging.getLogger(__name__)

LOCATION_QUESTION = {
    "en": "Where are you right now?",
    "es": "¿Dónde estás ahora mismo?",
}

WARNING_LEAD_IN = {
    "en": "Go to the ER or call 911 if:",
    "es": "Ve a la sala de emergencias o llama al 911 si:",
}

# Used when Gemini is down: Text 1 still goes out and the flow continues.
FIRST_AID_FALLBACK = {
    "en": (
        "I can't pull up first-aid steps right now. If it gets worse, or you have trouble breathing, "
        "chest pain, heavy bleeding, or confusion, call 911. I can still find care near you."
    ),
    "es": (
        "No puedo mostrar pasos de primeros auxilios en este momento. Si empeora, o tienes dificultad "
        "para respirar, dolor en el pecho, sangrado fuerte o confusión, llama al 911. "
        "Aún puedo buscar atención cerca de ti."
    ),
}

RED_FLAG_CATEGORIES = [
    "cardiac", "breathing", "stroke", "bleeding", "self_harm", "overdose", "unconscious",
    "severe_allergy", "pregnancy_emergency", "head_injury", "other_emergency", "none",
]

RED_FLAGS_TEXT = """\
Red flags (any one means emergency):
- chest pain, pressure or tightness; pain spreading to arm, jaw or back
- trouble breathing, choking, lips or face turning blue
- stroke signs: face drooping, arm or leg weakness on one side, slurred or confused speech, sudden vision loss
- severe or uncontrolled bleeding
- thoughts of suicide or self-harm, or wanting to die
- overdose or poisoning
- fainting, unconscious, unresponsive, seizure
- severe allergic reaction: swelling of face, lips or throat, hives with trouble breathing
- heavy bleeding or severe pain during pregnancy
- head injury with confusion, vomiting, or passing out
- fever in a baby under 3 months"""

FIRST_AID_SYSTEM = f"""\
You are the first reply of a text-message health navigator in San Diego. You are not a doctor.

{RED_FLAGS_TEXT}

If the message shows ANY red flag, or you are unsure whether it does, set red_flag to true and
pick the closest red_flag_category. When red_flag is true, the reply text is ignored.

Otherwise set red_flag to false, red_flag_category to "none", and write the reply:
- Never diagnose and never name a condition as what they have. Talk about what they described.
- 2 to 4 short general first-aid steps anyone can do at home now.
- Then the warning lead-in for the reply's language, exactly as given below, followed by 2 or 3
  warning signs specific to what they described.
- No medication doses. No markdown, no asterisks, no bullets. No greeting or sympathy sentence.
- Under 380 characters. Plain, kind, direct.
- Do NOT ask where they are; that question is added after your text."""

FIRST_AID_SCHEMA = {
    "type": "object",
    "properties": {
        "red_flag": {"type": "boolean"},
        "red_flag_category": {"type": "string", "enum": RED_FLAG_CATEGORIES},
        "reply": {"type": "string"},
    },
    "required": ["red_flag", "red_flag_category", "reply"],
}

LEVEL_SYSTEM = f"""\
Pick the level of care for a health concern sent by text. You are not diagnosing.

Levels, lowest to highest:
- self_care: minor, safe to manage at home
- telehealth: a video or phone visit can handle it (no exam or test needed)
- primary_care: needs a clinic visit, but can wait a day or more
- urgent_care: needs in-person care today: possible fracture or sprain (may need an X-ray),
  cuts that may need stitches, high fever, pain that is getting worse
- er: emergency

{RED_FLAGS_TEXT}
Any red flag means er.

When torn between two levels, choose the higher one and give a lower confidence.
confidence is your probability (0 to 1) that the chosen level is right."""

LEVEL_SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": [lvl.value for lvl in LEVEL_ORDER]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["level", "confidence"],
}


def _lang(profile: Profile) -> str:
    return "es" if (profile.language or "en").lower().startswith("es") else "en"


def _language_instruction(lang: str) -> str:
    primary, other = ("Spanish", "English") if lang == "es" else ("English", "Spanish")
    return (
        f"Write the reply in {primary}. If the user's message is clearly written in {other}, "
        f"write it in {other} instead, and set reply_language accordingly.\n"
        f'Warning lead-in in English: "{WARNING_LEAD_IN["en"]}"\n'
        f'Warning lead-in in Spanish: "{WARNING_LEAD_IN["es"]}"'
    )


PHYSICAL_CATEGORIES = set(RED_FLAG_CATEGORIES) - {"self_harm", "overdose", "other_emergency", "none"}


def _emergency_text(category: str | None, lang: str) -> str:
    """The same fixed text app.safety.screen() sends, so both nets read the same.

    self_harm: 988 first. A clear physical category: 911. Overdose, other, or a missing or
    unknown category: 911 plus 988, because Gemini's category may be wrong.
    """
    if category == "self_harm":
        return SELF_HARM_REPLY[lang]
    if category in PHYSICAL_CATEGORIES:
        return EMERGENCY_REPLY[lang]
    return SELF_HARM_WITH_EMERGENCY[lang]


_MARKDOWN = re.compile(r"[*_#`]+")


def _clean(reply: str) -> str:
    reply = _MARKDOWN.sub("", reply).strip()
    for q in LOCATION_QUESTION.values():  # Gemini sometimes asks anyway; we add it ourselves
        if reply.endswith(q):
            reply = reply[: -len(q)].rstrip()
    return reply


def first_aid_reply(text: str, profile: Profile) -> TriageResult:
    """Gemini: second-net red-flag check + general first-aid steps + ER warning signs,
    ending with 'Where are you right now?'. Reply in profile.language (or the language the
    user wrote in). Never raises: if Gemini fails, returns a fixed fallback Text 1."""
    lang = _lang(profile)
    schema = {
        **FIRST_AID_SCHEMA,
        "properties": {**FIRST_AID_SCHEMA["properties"], "reply_language": {"type": "string", "enum": ["en", "es"]}},
        "required": [*FIRST_AID_SCHEMA["required"], "reply_language"],
    }
    try:
        out = generate_json(
            prompt=f"User's message:\n{text}",
            schema=schema,
            system=FIRST_AID_SYSTEM + "\n" + _language_instruction(lang),
        )
    except GeminiError:
        log.exception("first_aid_reply: Gemini failed, sending fallback")
        return TriageResult(red_flag=False, reply=f"{FIRST_AID_FALLBACK[lang]}\n\n{LOCATION_QUESTION[lang]}")

    if out.get("red_flag") is True:
        return TriageResult(red_flag=True, reply=_emergency_text(out.get("red_flag_category"), lang))

    reply_lang = out.get("reply_language") if out.get("reply_language") in ("en", "es") else lang
    body = _clean(str(out.get("reply") or ""))
    if not body:
        body = FIRST_AID_FALLBACK[reply_lang]
    return TriageResult(red_flag=False, reply=f"{body}\n\n{LOCATION_QUESTION[reply_lang]}")


def level_of_care(text: str, profile: Profile) -> tuple[CareLevel, float]:
    """Gemini picks one of LEVEL_ORDER plus a confidence in [0, 1].

    Raises GeminiError if Gemini fails. The caller (#17) decides the fallback; passing
    (CareLevel.URGENT_CARE, 0.0) through bump_if_unsure is the conservative choice.
    """
    out = generate_json(prompt=f"User's message:\n{text}", schema=LEVEL_SCHEMA, system=LEVEL_SYSTEM)
    try:
        level = CareLevel(out["level"])
        confidence = float(out["confidence"])
    except (KeyError, ValueError, TypeError) as e:
        raise GeminiError(f"bad level_of_care output: {out}") from e
    return level, min(max(confidence, 0.0), 1.0)


def bump_if_unsure(level: CareLevel, confidence: float, threshold: float = 0.7) -> CareLevel:
    """Pure. If confidence < threshold, move one step up LEVEL_ORDER (ER stays ER)."""
    if confidence >= threshold:
        return level
    i = LEVEL_ORDER.index(level)
    return LEVEL_ORDER[min(i + 1, len(LEVEL_ORDER) - 1)]
