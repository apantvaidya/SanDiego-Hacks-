"""English and Spanish strings. Keys shared by onboarding, render, followup, safety.

t(key, language, **kwargs) formats TEMPLATES[language][key] with kwargs. Unknown languages
fall back to English, and a key missing in Spanish falls back to the English string.
A key missing in English raises KeyError, so typos fail loudly in tests.

Other issues add keys here (#9, #10, #16). Keep each issue's keys in its own block, sorted
by prefix, so parallel branches merge cleanly.
"""
from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        # ---- render (#7) ------------------------------------------------------
        "render.header": "Options open now:",
        "render.option_title": "{n}. {name} ({miles} mi)",
        "render.open_until": "Open until {time}",
        "render.open_24h": "Open 24 hours",
        "render.walk_in": "Walk-in",
        "render.xray_yes": "X-ray on site",
        "render.xray_unknown": "Call ahead about X-ray",
        "render.xray_no": "No X-ray",
        "render.cost": "Cost for you: {cost}",
        "render.why": "Why: {reason}",
        "render.contact": "{address} · {phone}",
        "render.contact_no_phone": "{address}",
        "render.source_verified": "Source: {url} (verified {minutes} min ago)",
        "render.source_verified_closed": "Source: {url} (live check {minutes} min ago says it may be closed, call first)",
        "render.source_checked": "Source: {url} (checked {when})",
        "render.tradeoff_uninsured": (
            "The tradeoff: the community clinic's sliding fee is usually cheapest, but it may not have "
            "X-ray and can stop taking walk-ins when busy. Urgent care costs more and has X-ray."
        ),
        "render.er_note": "Go to the ER instead if it gets much worse or you see any of the warning signs above.",
        "cost.medi_cal": "$0 with Medi-Cal",
        "cost.copay": "{amount} copay",
        "cost.copay_unknown": "your plan's copay (amount not confirmed)",
        "cost.self_pay": "{amount} self-pay",
        "cost.sliding_scale": "{amount} on the sliding fee scale",
        "cost.sliding_scale_unknown": "sliding fee based on your income",
        "cost.unknown": "not confirmed, ask when you call",
        "cost.range": "{low} to {high}",
        "fallback.none": "I don't have verified options open near you right now.",
        "fallback.nurse_line": "Your plan's nurse advice line: {phone}",
        "fallback.telehealth": "Your plan covers telehealth visits. Check your plan's app or website.",
        "fallback.211": "Call 211 San Diego (dial 2-1-1) to find an open clinic near you.",
        "fallback.911": "If it gets worse, call 911.",
    },
    "es": {
        # ---- render (#7) ------------------------------------------------------
        "render.header": "Opciones abiertas ahora:",
        "render.option_title": "{n}. {name} ({miles} mi)",
        "render.open_until": "Abierto hasta las {time}",
        "render.open_24h": "Abierto 24 horas",
        "render.walk_in": "Sin cita",
        "render.xray_yes": "Rayos X en el lugar",
        "render.xray_unknown": "Llama antes para preguntar por rayos X",
        "render.xray_no": "Sin rayos X",
        "render.cost": "Costo para ti: {cost}",
        "render.why": "Por qué: {reason}",
        "render.contact": "{address} · {phone}",
        "render.contact_no_phone": "{address}",
        "render.source_verified": "Fuente: {url} (verificado hace {minutes} min)",
        "render.source_verified_closed": "Fuente: {url} (una revisión hace {minutes} min indica que puede estar cerrado, llama antes)",
        "render.source_checked": "Fuente: {url} (revisado {when})",
        "render.tradeoff_uninsured": (
            "La diferencia: la tarifa de escala móvil de la clínica comunitaria suele ser la más barata, pero "
            "puede no tener rayos X y puede dejar de aceptar pacientes sin cita cuando está llena. "
            "La atención urgente cuesta más y tiene rayos X."
        ),
        "render.er_note": "Ve a la sala de emergencias si empeora mucho o notas alguna de las señales de alarma de arriba.",
        "cost.medi_cal": "$0 con Medi-Cal",
        "cost.copay": "copago de {amount}",
        "cost.copay_unknown": "el copago de tu plan (monto no confirmado)",
        "cost.self_pay": "{amount} pagando por tu cuenta",
        "cost.sliding_scale": "{amount} con la tarifa de escala móvil",
        "cost.sliding_scale_unknown": "tarifa de escala móvil según tus ingresos",
        "cost.unknown": "no confirmado, pregunta cuando llames",
        "cost.range": "{low} a {high}",
        "fallback.none": "No tengo opciones verificadas abiertas cerca de ti en este momento.",
        "fallback.nurse_line": "Línea de enfermería de tu plan: {phone}",
        "fallback.telehealth": "Tu plan cubre consultas por telesalud. Revisa la app o el sitio web de tu plan.",
        "fallback.211": "Llama al 211 San Diego (marca 2-1-1) para encontrar una clínica abierta cerca de ti.",
        "fallback.911": "Si empeora, llama al 911.",
    },
}


def _lang(language: str | None) -> str:
    return "es" if (language or "en").lower().startswith("es") else "en"


def t(key: str, language: str, **kwargs) -> str:
    lang = _lang(language)
    template = TEMPLATES[lang].get(key)
    if template is None:
        template = TEMPLATES["en"][key]  # KeyError if the key is missing everywhere
    return template.format(**kwargs) if kwargs else template
