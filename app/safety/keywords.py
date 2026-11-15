"""Hard-coded red-flag screen. Runs before ANY LLM call. No network, no LLM.

Design choices:
- Recall over precision. A false alarm costs one extra text; a miss can cost a life.
  Example: "my chest hurts after my workout" still triggers the cardiac reply.
- Simple negation guard: a match is skipped only when "no / not / don't / without /
  sin / nunca" appears in the 3 words just before it, inside the same clause.
  "no chest pain, just my ankle" passes; "no, I have chest pain" still triggers.
- Replies are fixed strings, never generated. Self-harm adds 988.
- Infant fever is only caught when the age (under 3 months) is in the text. A baby
  fever with no age goes to the Gemini second net.

Sources for the two added categories:
- Bleeding during pregnancy: CDC Hear Her urgent maternal warning signs,
  https://www.cdc.gov/hearher/maternal-warning-signs/index.html
- Fever in a baby under 3 months (100.4 F / 38 C): call right away (AAP guidance as cited by
  https://www.mayoclinic.org/healthy-lifestyle/infant-and-toddler-health/in-depth/art-20047793)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models import SafetyResult

# ---------------------------------------------------------------------------
# Fixed replies
# ---------------------------------------------------------------------------

EMERGENCY_REPLY = {
    "en": (
        "This could be an emergency. Call 911 or go to the nearest emergency room now. "
        "If you feel faint, have chest pain, or trouble breathing, don't drive yourself."
    ),
    "es": (
        "Esto podría ser una emergencia. Llama al 911 o ve a la sala de emergencias más cercana ahora. "
        "Si te sientes débil, tienes dolor de pecho o te cuesta respirar, no manejes tú."
    ),
}

SELF_HARM_REPLY = {
    "en": (
        "I'm really glad you reached out. Please call or text 988 now. It's the Suicide & Crisis "
        "Lifeline: free, private, and open 24/7. If you're in immediate danger, call 911."
    ),
    "es": (
        "Me alegra mucho que hayas escrito. Por favor llama o envía un mensaje de texto al 988 ahora. "
        "Es la Línea de Prevención del Suicidio y Crisis: gratis, confidencial y abierta 24/7, en español. "
        "Si estás en peligro inmediato, llama al 911."
    ),
}

# Shown in the welcome text and the HELP reply.
NOT_FOR_EMERGENCIES = {
    "en": "This is not for emergencies. If you think it's an emergency, call 911. For a mental health crisis, call or text 988.",
    "es": "Esto no es para emergencias. Si crees que es una emergencia, llama al 911. Para una crisis de salud mental, llama o envía un texto al 988.",
}

# ---------------------------------------------------------------------------
# Patterns (run on normalized text: lowercase, no accents, apostrophes removed)
# ---------------------------------------------------------------------------

W = r"(?:\s+\S+){0,6}?\s+"  # up to ~6 words in between
BREATHE = r"(?:breathe|breath|breth|breathing|breating)"


@dataclass(frozen=True)
class _Rule:
    category: str
    pattern: re.Pattern
    language: str  # language the pattern is written in


def _rules(category: str, language: str, *patterns: str) -> list[_Rule]:
    return [_Rule(category, re.compile(p), language) for p in patterns]


RULES: list[_Rule] = [
    # --- cardiac ---
    *_rules(
        "cardiac", "en",
        r"\bchest\s*(?:pain|pian|pains|pressure|tightness|tight|hurts?|hurting|discomfort|squeez\w*|heavy|heaviness)\b",
        r"\b(?:pain|pressure|tightness|squeezing|heaviness)\s+(?:in|on)\s+(?:my\s+|his\s+|her\s+|the\s+)?chest\b",
        r"\bchest\s+(?:is|feels|felt)\s+(?:tight|heavy|crushed|like)\b",
        r"\bheart\s*attack\b",
        r"\bcardiac\s+arrest\b",
    ),
    *_rules(
        "cardiac", "es",
        r"\bdolor\s+(?:de|en\s+el|en)\s+(?:el\s+)?pecho\b",
        r"\b(?:presion|opresion)\s+en\s+el\s+pecho\b",
        r"\bme\s+duele\s+el\s+pecho\b",
        r"\bataque\s+(?:al|de)\s+corazon\b",
        r"\binfarto\b",
    ),
    # --- breathing ---
    *_rules(
        "breathing", "en",
        rf"\b(?:cant|cannot|can\s+not|couldnt|unable\s+to|not\s+able\s+to|struggling\s+to|hard\s+to)\s+{BREATHE}\b",
        rf"\b(?:trouble|difficulty|problems?|hard\s+time)\s+{BREATHE}\b",
        rf"\bshort(?:ness)?\s+of\s+{BREATHE}\b",
        rf"\bnot\s+{BREATHE}\b",
        r"\bstopped\s+breathing\b",
        r"\bgasping\b",
        r"\bchoking\b",
        r"\b(?:turning|turned|is|are)\s+blue\b",
        r"\blips\s+(?:are\s+|look\s+)?(?:blue|gray|grey)\b",
    ),
    *_rules(
        "breathing", "es",
        r"\bno\s+(?:puedo|puede|podia)\s+respirar\b",
        r"\b(?:dificultad|problemas?)\s+para\s+respirar\b",
        r"\bfalta\s+de\s+aire\b",
        r"\bme\s+cuesta\s+respirar\b",
        r"\b(?:me\s+ahogo|se\s+(?:esta\s+)?ahog\w*|ahogandose)\b",
    ),
    # --- stroke ---
    *_rules(
        "stroke", "en",
        r"\bstroke\b",
        r"\bface\s+(?:is\s+)?droop\w*\b",
        r"\bdroop\w*\s+(?:face|mouth|smile)\b",
        r"\bslurr\w*\b",
        r"\bone\s+side\s+of\s+(?:my|his|her|their|the)\s+(?:face|body)\b",
        r"\b(?:sudden\w*)\s+(?:numb\w*|weak\w*|confus\w*|blind\w*)\b",
        r"\b(?:cant|cannot|can\s+not)\s+(?:move|feel|lift)\s+(?:my|his|her|their)\s+(?:arm|face|left\s+side|right\s+side)\b",
        r"\bworst\s+headache\b",
        r"\bsudden\s+(?:severe|bad|terrible)\s+headache\b",
    ),
    *_rules(
        "stroke", "es",
        r"\bderrame(?:\s+cerebral)?\b",
        r"\bembolia\b",
        r"\bcara\s+(?:caida|torcida|chueca)\b",
        r"\bhabla\s+(?:arrastrada|rara|enredada)\b",
        r"\bno\s+puede\s+hablar\b",
        r"\bun\s+lado\s+de\s+(?:la\s+cara|el\s+cuerpo)\b",
    ),
    # --- severe bleeding ---
    *_rules(
        "bleeding", "en",
        r"\bbleed\w*\s+(?:a\s+lot|heavily|badly|everywhere|nonstop|non\s+stop)\b",
        r"\bbleed\w*" + W + r"(?:wont|will\s+not|doesnt|isnt|not)\s+stop",
        r"\b(?:wont|cant|cannot|can\s+not)\s+stop\s+(?:the\s+)?bleed\w*\b",
        r"\b(?:heavy|severe|uncontrolled|massive)\s+bleeding\b",
        r"\b(?:blood\s+(?:is\s+)?(?:spurting|gushing|pouring|everywhere))\b",
        r"\b(?:lots|lot|tons)\s+of\s+blood\b",
        r"\blosing\s+(?:a\s+lot\s+of\s+)?blood\b",
        r"\b(?:coughing|cough|vomiting|throwing)\s+(?:up\s+)?blood\b",
    ),
    *_rules(
        "bleeding", "es",
        r"\bsangrado\s+(?:abundante|fuerte|grave|que\s+no\s+para)\b",
        r"\bno\s+(?:para|deja)\s+de\s+sangrar\b",
        r"\bmucha\s+sangre\b",
        r"\b(?:vomitando|tosiendo|vomito)\s+sangre\b",
    ),
    # --- self-harm ---
    *_rules(
        "self_harm", "en",
        r"\bkill\s+(?:my\s*self|myself|me)\b",
        r"\bsuicid\w*\b",
        r"\b(?:sucid|suicd|suisid)\w*\b",
        r"\b(?:want|wanna|going|gonna|wish\s+i\s+could)\s+(?:to\s+)?die\b",
        r"\bend\s+(?:my\s+life|it\s+all|everything)\b",
        r"\b(?:hurt|hurting|harm|harming|cut|cutting)\s+myself\b",
        r"\bself\s*harm\w*\b",
        r"\bdont\s+want\s+to\s+(?:live|be\s+alive|be\s+here|exist)\b",
        r"\bbetter\s+off\s+dead\b",
        r"\bno\s+reason\s+to\s+live\b",
    ),
    *_rules(
        "self_harm", "es",
        r"\bsuicid\w*\b",
        r"\bquiero\s+morir\w*\b",
        r"\bmatarme\b",
        r"\bquitarme\s+la\s+vida\b",
        r"\bhacerme\s+dano\b",
        r"\bno\s+quiero\s+vivir\b",
    ),
    # --- overdose / poisoning ---
    *_rules(
        "overdose", "en",
        r"\boverdos\w*\b",
        r"\btook\s+(?:too\s+many|a\s+bunch\s+of|a\s+lot\s+of|a\s+whole\s+bottle\s+of|all\s+(?:my|the))\s+(?:pills|tablets|meds|medication\w*)\b",
        r"\b(?:swallowed|drank|ate)\s+(?:bleach|poison|antifreeze|detergent|a\s+battery|pills)\b",
        r"\b(?<!food )poison(?!\s+(?:ivy|oak|sumac))\w*\b",
        r"\bfentanyl\b",
    ),
    *_rules(
        "overdose", "es",
        r"\bsobredosis\b",
        r"\b(?:tomo|tome|se\s+tomo)\s+(?:muchas|demasiadas|todas\s+las)\s+pastillas\b",
        r"\benvenen\w*\b",
        r"\b(?:tomo|tome|se\s+tomo)\s+(?:cloro|veneno)\b",
    ),
    # --- unconscious / seizure ---
    *_rules(
        "unconscious", "en",
        r"\bunconscious\b",
        r"\bunresponsive\b",
        r"\b(?:wont|isnt|not|cant)\s+wak\w*\s*up\b",
        r"\bpassed\s+out\b",
        r"\bfainted\b",
        r"\bcollapsed\b",
        r"\bseizure\w*\b",
        r"\bconvuls\w*\b",
    ),
    *_rules(
        "unconscious", "es",
        r"\binconsciente\b",
        r"\bno\s+(?:despierta|responde|reacciona)\b",
        r"\bse\s+desmayo\b",
        r"\bdesmayad\w*\b",
        r"\bconvulsi\w*\b",
    ),
    # --- severe allergic reaction ---
    *_rules(
        "severe_allergy", "en",
        r"\banaphyla\w*\b",
        r"\bthroat\s+(?:is\s+|feels\s+)?(?:closing|swelling|swollen|tight)\b",
        r"\b(?:tongue|lips|face)\s+(?:is\s+|are\s+)?(?:swelling|swollen)\b",
        r"\bswelling\s+(?:of|in)\s+(?:my\s+|his\s+|her\s+)?(?:throat|tongue)\b",
        r"\b(?:severe|bad)\s+allergic\s+reaction\b",
        r"\bepi\s*pen\b",
    ),
    *_rules(
        "severe_allergy", "es",
        r"\banafila\w*\b",
        r"\b(?:se\s+me\s+cierra|se\s+cierra|cerrando)\s+la\s+garganta\b",
        r"\bgarganta\s+(?:cerrada|hinchada)\b",
        r"\blengua\s+hinchada\b",
        r"\breaccion\s+alergica\s+(?:grave|fuerte|severa)\b",
    ),
    # --- head injury with warning signs ---
    *_rules(
        "head_injury", "en",
        r"\b(?:hit|hurt|bang\w*|bump\w*|injur\w*|smack\w*|struck)\s+(?:my|his|her|their|the)\s+head\b.{0,60}\b(?:confus\w*|vomit\w*|throw\w*\s+up|threw\s+up|pass\w*\s+out|black\w*\s+out|knocked\s+out|cant\s+remember|drowsy|seizure)",
        r"\bknocked\s+(?:out|unconscious)\b",
        r"\bblacked\s+out\b",
        r"\bconcussion\b.{0,40}\b(?:vomit\w*|confus\w*|pass\w*\s+out)",
    ),
    *_rules(
        "head_injury", "es",
        r"\bgolpe\s+en\s+la\s+cabeza\b.{0,60}\b(?:confundid\w*|vomit\w*|desmay\w*|no\s+recuerda)",
        r"\bse\s+(?:pego|golpeo)\s+(?:en\s+)?la\s+cabeza\b.{0,60}\b(?:confundid\w*|vomit\w*|desmay\w*)",
    ),
    # --- bleeding during pregnancy (CDC Hear Her) ---
    *_rules(
        "pregnancy", "en",
        r"\bpregnan\w*\b.{0,60}\b(?:bleed\w*|blood|fluid\s+leak\w*|leaking\s+fluid|baby\s+(?:stopped|isnt|is\s+not)\s+moving)",
        r"\b(?:bleed\w*|blood)\b.{0,60}\bpregnan\w*",
    ),
    *_rules(
        "pregnancy", "es",
        r"\bembarazada\b.{0,60}\bsangr\w*",
        r"\bsangr\w*\b.{0,60}\bembaraz\w*",
    ),
    # --- fever in a baby under 3 months (age must be stated) ---
    *_rules(
        "infant_fever", "en",
        r"\b(?:newborn|(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:day|week|wk)s?\s*old|(?:1|2|one|two)\s*months?\s*old)\b.{0,60}\b(?:fever|temp\w*|hot|burning\s+up)",
        r"\b(?:fever|temp\w*)\b.{0,60}\b(?:newborn|(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:day|week|wk)s?\s*old|(?:1|2|one|two)\s*months?\s*old)\b",
    ),
    *_rules(
        "infant_fever", "es",
        r"\b(?:recien\s+nacid\w*|bebe\s+de\s+(?:\d{1,2}|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+(?:dias?|semanas?)|bebe\s+de\s+(?:1|2|un|dos)\s+mes(?:es)?)\b.{0,60}\b(?:fiebre|temperatura|calentura)",
        r"\b(?:fiebre|temperatura|calentura)\b.{0,60}\b(?:recien\s+nacid\w*|(?:\d{1,2}|un|una|dos|tres)\s+(?:dias?|semanas?)\s+de\s+nacid\w*)",
    ),
]

_NEGATIONS = {"no", "not", "dont", "doesnt", "didnt", "isnt", "never", "without", "denies", "sin", "nunca", "tampoco"}
_CLAUSE_BREAK = re.compile(r"[.,;:!?\n]|\bbut\b|\bpero\b")


def normalize(text: str) -> str:
    """Lowercase, strip accents, drop apostrophes (can't -> cant), collapse spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("’", "").replace("'", "").replace("`", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def _negated(norm: str, start: int, match_text: str) -> bool:
    # A pattern that is itself negative ("cant breathe", "no puedo respirar") is never negated.
    if match_text.split()[0] in _NEGATIONS:
        return False
    before = _CLAUSE_BREAK.split(norm[:start])[-1]
    window = before.split()[-3:]
    return any(word in _NEGATIONS for word in window)


SELF_HARM_WITH_EMERGENCY = {
    "en": EMERGENCY_REPLY["en"] + " You can also call or text 988 any time, free and 24/7.",
    "es": EMERGENCY_REPLY["es"] + " También puedes llamar o enviar un texto al 988 en cualquier momento, gratis y 24/7.",
}


def screen(text: str, language: str = "en") -> SafetyResult:
    """Return a fixed emergency reply if the text contains a red flag.

    Self-harm matches ignore the negation guard ("I'm not suicidal" still gets 988).
    Self-harm plus a physical emergency (for example an overdose) gets 911 and 988.
    """
    if not text:
        return SafetyResult(matched=False)
    norm = normalize(text)
    hits: list[_Rule] = []
    for rule in RULES:
        for m in rule.pattern.finditer(norm):
            if rule.category != "self_harm" and _negated(norm, m.start(), m.group(0)):
                continue
            hits.append(rule)
            break
    if not hits:
        return SafetyResult(matched=False)

    spanish = language == "es" or any(r.language == "es" for r in hits)
    lang = "es" if spanish else "en"
    categories = [r.category for r in hits]
    physical = [c for c in categories if c != "self_harm"]
    if "self_harm" in categories and physical:
        return SafetyResult(matched=True, category="self_harm", reply=SELF_HARM_WITH_EMERGENCY[lang])
    if "self_harm" in categories:
        return SafetyResult(matched=True, category="self_harm", reply=SELF_HARM_REPLY[lang])
    return SafetyResult(matched=True, category=physical[0], reply=EMERGENCY_REPLY[lang])
