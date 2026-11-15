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
        # ---- pipeline (#17) ---------------------------------------------------
        "pipeline.ask_zip": "I couldn't find that place in San Diego County. What's your ZIP code?",
        # ---- onboarding (#10) -------------------------------------------------
        "onboarding.welcome": "Hi, I'm Care Navigator. Text me any health question and I'll tell you what to do, where to go nearby, and what it should cost you.",
        "onboarding.ask_card": "To start, send a photo of the front of your insurance card. No card? Reply NONE, MEDI-CAL, or OTHER.",
        "onboarding.card_unreadable": "I couldn't read that card. Try another photo in good light, or type your insurance name. You can also reply NONE, MEDI-CAL, or OTHER.",
        "onboarding.confirm_card": "Looks like {plan}. Right? (Y/N)",
        "onboarding.your_insurance": "your insurance",
        "onboarding.ask_medi_cal_plan": "Which Medi-Cal plan is on your card?\n1. Community Health Group\n2. Another plan or not sure",
        "onboarding.ask_plan_name": "What's the name of your insurance? Type it as it appears on your card.",
        "onboarding.ask_plan_name_or_options": "No problem. Type your insurance name, or reply NONE, MEDI-CAL, or OTHER.",
        "onboarding.ask_household": "How many people are in your household, including you?",
        "onboarding.ask_household_again": "Please reply with a number, like 1 or 4.",
        "onboarding.ask_income": "What's your household's yearly income before taxes? ({n} people)",
        "onboarding.income_under": "Under {high}",
        "onboarding.income_between": "{low} to {high}",
        "onboarding.income_over": "Over {low}",
        "onboarding.income_skip": "5. Skip",
        "onboarding.income_why": "This unlocks discounts at clinics. I only keep the range.",
        "onboarding.ask_language": "English or Español? Reply 1 for English, 2 para Español.",
        "onboarding.done": "You're set up with {plan}. Text me any health question, any time. I keep only your plan, household size and income range, never your card or member ID.",
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
        # ---- pipeline (#17) ---------------------------------------------------
        "pipeline.ask_zip": "No encontré ese lugar en el condado de San Diego. ¿Cuál es tu código postal?",
        # ---- onboarding (#10) -------------------------------------------------
        "onboarding.welcome": "Hola, soy Care Navigator. Escríbeme cualquier pregunta de salud y te diré qué hacer, adónde ir cerca de ti y cuánto debería costarte.",
        "onboarding.ask_card": "Para empezar, envía una foto del frente de tu tarjeta de seguro. ¿No tienes tarjeta? Responde NINGUNO, MEDI-CAL u OTRO.",
        "onboarding.card_unreadable": "No pude leer esa tarjeta. Intenta otra foto con buena luz, o escribe el nombre de tu seguro. También puedes responder NINGUNO, MEDI-CAL u OTRO.",
        "onboarding.confirm_card": "Parece que es {plan}. ¿Correcto? (S/N)",
        "onboarding.your_insurance": "tu seguro",
        "onboarding.ask_medi_cal_plan": "¿Qué plan de Medi-Cal aparece en tu tarjeta?\n1. Community Health Group\n2. Otro plan o no estoy seguro",
        "onboarding.ask_plan_name": "¿Cómo se llama tu seguro? Escríbelo como aparece en tu tarjeta.",
        "onboarding.ask_plan_name_or_options": "No hay problema. Escribe el nombre de tu seguro, o responde NINGUNO, MEDI-CAL u OTRO.",
        "onboarding.ask_household": "¿Cuántas personas hay en tu hogar, contándote a ti?",
        "onboarding.ask_household_again": "Responde con un número, por ejemplo 1 o 4.",
        "onboarding.ask_income": "¿Cuál es el ingreso anual de tu hogar antes de impuestos? ({n} personas)",
        "onboarding.income_under": "Menos de {high}",
        "onboarding.income_between": "De {low} a {high}",
        "onboarding.income_over": "Más de {low}",
        "onboarding.income_skip": "5. Prefiero no decir",
        "onboarding.income_why": "Esto te da descuentos en clínicas. Solo guardo el rango.",
        "onboarding.ask_language": "English or Español? Reply 1 for English, 2 para Español.",
        "onboarding.done": "Listo, quedaste registrado con {plan}. Escríbeme cualquier pregunta de salud cuando quieras. Solo guardo tu plan, el tamaño de tu hogar y el rango de ingresos, nunca tu tarjeta ni tu número de miembro.",
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
