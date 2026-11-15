"""Text onboarding: card photo (or NONE / MEDI-CAL / OTHER) -> confirm -> household ->
income bracket -> language. Never store member ID or the image. Owned by #10.

States (db/schema.sql), all set here through app.conversation.store (#9):
  new                   -> welcome, ask for the card            -> onboarding_card
  onboarding_card       photo: Gemini vision reads the card     -> onboarding_confirm
                        NONE: uninsured                         -> onboarding_household
                        MEDI-CAL: which plan? (sub-step)        -> onboarding_household
                        OTHER / typed name: mapped by keyword   -> onboarding_confirm
  onboarding_confirm    Y -> onboarding_household; N -> back to onboarding_card
  onboarding_household  1 to 20                                 -> onboarding_income
  onboarding_income     1 to 4 or 5 = skip                      -> onboarding_language
  onboarding_language   1 English / 2 Español -> profile saved and made active -> idle

Answers collect in conversations.context ("onb") and become a profile only at the end;
the context is cleared then (CLAUDE.md rule 6). Privacy (rule 6): the card image bytes live
only inside handle_onboarding and are never written anywhere; the vision prompt does not
ask for, and the schema has no field for, a member ID.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from app.conversation.fpl import bracket_bounds
from app.i18n.templates import t
from app.llm.gemini import GeminiError, generate_json
from app.models import CardExtraction, CoverageType, FplRange, InboundMessage, User

log = logging.getLogger(__name__)

PROFILE_LABEL = "me"
CONFIDENCE_MIN = 0.6

# Plan ids that exist in the plans table. Mapping is deliberately small: a plan_id must
# reference a plans row (FK), and we only have benefits for these two.
KAISER_PLAN_ID = "kaiser_cc_silver"   # stands in for any Kaiser plan until more are extracted (#14)
CHG_PLAN_ID = "chg_medi_cal"

CARD_SYSTEM = """\
You read a photo of a US health insurance card for a text-message health navigator.
Return only: the insurer (company) name, the plan or product name as printed, the coverage
type, and your confidence (0 to 1) that this is a readable insurance card.
Coverage type: kaiser (Kaiser Permanente), medi_cal (Medi-Cal / Medicaid in California, including
managed care plans such as Community Health Group, Blue Shield Promise, Molina), covered_ca
(Covered California marketplace plan, not Kaiser), medicare, commercial (employer or other private
insurance), or unknown.
Do NOT read or return member ID, group number, names, dates of birth, or any other personal data.
If the image is not an insurance card, return confidence 0."""

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "insurer": {"type": ["string", "null"]},
        "plan_name": {"type": ["string", "null"]},
        "coverage_type": {"type": "string",
                          "enum": ["kaiser", "medi_cal", "covered_ca", "medicare", "commercial", "unknown"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["insurer", "plan_name", "coverage_type", "confidence"],
}

_SPANISH_HINTS = re.compile(r"\b(hola|ayuda|necesito|tengo|duele|dolor|espanol|buenas|gracias|seguro|por favor)\b")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def _store():
    from app.conversation import store  # #9
    return store


# --------------------------------------------------------------------------- card reading

def extract_card(image: bytes) -> CardExtraction:
    """Gemini vision on the card photo. Insurer, plan name, coverage type, confidence. No member ID."""
    try:
        out = generate_json(prompt="Read this insurance card.", schema=CARD_SCHEMA, image=image, system=CARD_SYSTEM)
    except GeminiError:
        log.exception("extract_card: Gemini failed")
        return CardExtraction(insurer=None, plan_name=None, coverage_type=None, confidence=0.0)
    cov = out.get("coverage_type")
    try:
        coverage = CoverageType(cov) if cov and cov != "unknown" else None
    except ValueError:
        coverage = None
    conf = out.get("confidence")
    return CardExtraction(
        insurer=(out.get("insurer") or None),
        plan_name=(out.get("plan_name") or None),
        coverage_type=coverage,
        confidence=min(max(float(conf), 0.0), 1.0) if isinstance(conf, (int, float)) else 0.0,
    )


def plan_from(insurer: str | None, plan_name: str | None, coverage: CoverageType | None) -> dict:
    """Map an insurer / plan name / coverage to the profile fields we store."""
    text = _norm(f"{insurer or ''} {plan_name or ''}")
    if "kaiser" in text or coverage is CoverageType.KAISER:
        return {"coverage_type": CoverageType.KAISER.value, "insurer": "Kaiser Permanente", "plan_id": KAISER_PLAN_ID}
    if "community health group" in text or re.search(r"\bchg\b", text):
        return {"coverage_type": CoverageType.MEDI_CAL.value, "insurer": "Community Health Group", "plan_id": CHG_PLAN_ID}
    if coverage is CoverageType.MEDI_CAL or "medi cal" in text or "medical" in text.replace(" ", ""):
        return {"coverage_type": CoverageType.MEDI_CAL.value, "insurer": insurer, "plan_id": None}
    if coverage is CoverageType.MEDICARE or "medicare" in text:
        return {"coverage_type": CoverageType.MEDICARE.value, "insurer": insurer, "plan_id": None}
    if coverage is CoverageType.COVERED_CA or "covered california" in text:
        return {"coverage_type": CoverageType.COVERED_CA.value, "insurer": insurer, "plan_id": None}
    return {"coverage_type": CoverageType.COMMERCIAL.value, "insurer": insurer or plan_name, "plan_id": None}


def _describe(onb: dict, lang: str) -> str:
    if onb.get("plan_id") == KAISER_PLAN_ID:
        return "Kaiser Permanente"
    if onb.get("plan_id") == CHG_PLAN_ID:
        return "Community Health Group (Medi-Cal)"
    name = onb.get("plan_name") or onb.get("insurer")
    if onb.get("coverage_type") == CoverageType.MEDI_CAL.value:
        return f"{name} (Medi-Cal)" if name else "Medi-Cal"
    return name or t("onboarding.your_insurance", lang)


# --------------------------------------------------------------------------- parsing helpers

def _yes(text: str) -> bool | None:
    n = _norm(text)
    if n in {"y", "yes", "yeah", "yep", "yup", "si", "s", "correct", "right", "ok", "okay", "claro", "correcto"}:
        return True
    if n in {"n", "no", "nope", "nah", "incorrecto", "wrong"}:
        return False
    return None


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "just me": 1, "only me": 1, "solo yo": 1,
}


def _number(text: str) -> int | None:
    n = _norm(text)
    m = re.search(r"\b(\d{1,2})\b", n)
    if m:
        return int(m.group(1))
    for phrase, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{phrase}\b", n):
            return value
    return None


def _language_choice(text: str) -> str | None:
    n = _norm(text)
    if n in {"1", "en", "english", "ingles"} or "english" in n:
        return "en"
    if n in {"2", "es", "espanol", "spanish"} or "espanol" in n or "spanish" in n:
        return "es"
    return None


def guess_language(text: str) -> str:
    return "es" if _SPANISH_HINTS.search(_norm(text)) else "en"


def income_menu(household_size: int, lang: str) -> str:
    lines = [t("onboarding.ask_income", lang, n=household_size)]
    for i, (low, high, _tier) in enumerate(bracket_bounds(household_size), start=1):
        if low is None:
            label = t("onboarding.income_under", lang, high=f"${high:,}")
        elif high is None:
            label = t("onboarding.income_over", lang, low=f"${low:,}")
        else:
            label = t("onboarding.income_between", lang, low=f"${low:,}", high=f"${high:,}")
        lines.append(f"{i}. {label}")
    lines.append(t("onboarding.income_skip", lang))
    lines.append(t("onboarding.income_why", lang))
    return "\n".join(lines)


# --------------------------------------------------------------------------- steps

def _welcome(lang: str) -> str:
    from app.safety.keywords import NOT_FOR_EMERGENCIES
    return "\n\n".join([t("onboarding.welcome", lang), NOT_FOR_EMERGENCIES[lang], t("onboarding.ask_card", lang)])


def _go(user: User, state: str, onb: dict) -> None:
    _store().set_conversation(user.id, state, {"onb": onb})


def _after_coverage(user: User, onb: dict, lang: str) -> list[str]:
    _go(user, "onboarding_household", onb)
    return [t("onboarding.ask_household", lang)]


def _card_step(user: User, msg: InboundMessage, onb: dict, lang: str) -> list[str]:
    text = msg.text or ""
    n = _norm(text)

    if onb.get("awaiting") == "medi_cal_plan":
        onb.pop("awaiting")
        if n in {"1"} or "community" in n or re.search(r"\bchg\b", n):
            onb.update(plan_from("Community Health Group", None, CoverageType.MEDI_CAL))
        else:
            onb.update({"coverage_type": CoverageType.MEDI_CAL.value, "insurer": None, "plan_id": None})
        return _after_coverage(user, onb, lang)

    if msg.media_urls:
        from app.messaging import download_media
        try:
            image = download_media(msg.media_urls[0])
            card = extract_card(image)
        except Exception:
            log.exception("onboarding: card photo could not be read")
            card = CardExtraction(None, None, None, 0.0)
        finally:
            image = None  # noqa: F841  never kept beyond this call
        if card.confidence < CONFIDENCE_MIN or not (card.insurer or card.plan_name or card.coverage_type):
            _go(user, "onboarding_card", onb)
            return [t("onboarding.card_unreadable", lang)]
        onb.update(plan_from(card.insurer, card.plan_name, card.coverage_type))
        onb["plan_name"] = card.plan_name
        _go(user, "onboarding_confirm", onb)
        return [t("onboarding.confirm_card", lang, plan=_describe(onb, lang))]

    if n in {"none", "no insurance", "uninsured", "ninguno", "ninguna", "sin seguro", "no tengo", "no tengo seguro"}:
        onb.update({"coverage_type": CoverageType.UNINSURED.value, "insurer": None, "plan_id": None})
        return _after_coverage(user, onb, lang)
    if n in {"medi cal", "medical", "medicaid"}:
        onb["awaiting"] = "medi_cal_plan"
        _go(user, "onboarding_card", onb)
        return [t("onboarding.ask_medi_cal_plan", lang)]
    if n in {"other", "otro", "otra"}:
        _go(user, "onboarding_card", onb)
        return [t("onboarding.ask_plan_name", lang)]
    if not n:
        _go(user, "onboarding_card", onb)
        return [t("onboarding.ask_card", lang)]

    # A typed insurer or plan name.
    onb.update(plan_from(text.strip(), None, None))
    onb["plan_name"] = text.strip()[:80]
    _go(user, "onboarding_confirm", onb)
    return [t("onboarding.confirm_card", lang, plan=_describe(onb, lang))]


def handle_onboarding(user: User, msg: InboundMessage) -> list[str]:
    store = _store()
    state, context = store.get_conversation(user.id)
    onb: dict = dict(context.get("onb") or {})
    lang = onb.get("lang") or (user.language if user.language in ("en", "es") else "en")

    if state not in {"onboarding_card", "onboarding_confirm", "onboarding_household",
                     "onboarding_income", "onboarding_language"}:
        lang = guess_language(msg.text or "") if state == "new" else lang
        onb = {"lang": lang}
        _go(user, "onboarding_card", onb)
        return [_welcome(lang)]

    if state == "onboarding_card":
        return _card_step(user, msg, onb, lang)

    if state == "onboarding_confirm":
        answer = _yes(msg.text or "")
        if answer is True:
            return _after_coverage(user, onb, lang)
        if answer is False:
            onb = {"lang": lang}
            _go(user, "onboarding_card", onb)
            return [t("onboarding.ask_plan_name_or_options", lang)]
        return [t("onboarding.confirm_card", lang, plan=_describe(onb, lang))]

    if state == "onboarding_household":
        size = _number(msg.text or "")
        if size is None or not 1 <= size <= 20:
            return [t("onboarding.ask_household_again", lang)]
        onb["household_size"] = size
        _go(user, "onboarding_income", onb)
        return [income_menu(size, lang)]

    if state == "onboarding_income":
        choice = _number(msg.text or "")
        skip = _norm(msg.text or "") in {"skip", "saltar", "omitir", "prefer not", "no se"}
        if skip or choice == 5:
            onb["income_range_fpl"] = None
        elif choice in (1, 2, 3, 4):
            onb["income_range_fpl"] = list(FplRange)[choice - 1].value
        else:
            return [income_menu(int(onb.get("household_size") or 1), lang)]
        _go(user, "onboarding_language", onb)
        return [t("onboarding.ask_language", lang)]

    # onboarding_language
    chosen = _language_choice(msg.text or "")
    if chosen is None:
        return [t("onboarding.ask_language", lang)]
    profile = store.upsert_profile(
        user.id, PROFILE_LABEL,
        coverage_type=onb.get("coverage_type", CoverageType.UNINSURED.value),
        insurer=onb.get("insurer"), plan_id=onb.get("plan_id"),
        household_size=onb.get("household_size"), income_range_fpl=onb.get("income_range_fpl"),
        language=chosen,
    )
    store.set_active_profile(user.id, profile.id)
    store.set_language(user.id, chosen)
    store.set_conversation(user.id, "idle", {})
    return [t("onboarding.done", chosen, plan=_describe(onb, chosen))]
