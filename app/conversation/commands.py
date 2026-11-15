"""STOP / START / HELP and demo commands.

Keywords (case-insensitive, whole message): STOP, UNSUBSCRIBE, CANCEL, END, QUIT, ALTO;
START, UNSTOP; HELP, AYUDA, INFO.

Demo commands:
  #profile <kaiser|medi_cal|uninsured>   switch the active coverage profile
  #profile                               list profiles
  #lang <en|es>                          switch language
  #followup                              fire the next-day follow-up now
  #reset                                 clear the current conversation (keeps profiles)
  #reset all                             delete profiles too and start onboarding again
  #whoami                                show the active profile
"""
from __future__ import annotations

import re

from app.conversation import store
from app.models import User
from app.safety.keywords import NOT_FOR_EMERGENCIES

STOP_WORDS = {"stop", "unsubscribe", "cancel", "end", "quit", "stopall", "alto", "parar"}
START_WORDS = {"start", "unstop", "yes", "comenzar"}
HELP_WORDS = {"help", "info", "ayuda"}

PROFILE_ALIASES = {
    "kaiser": "kaiser",
    "kp": "kaiser",
    "medi_cal": "medi_cal",
    "medical": "medi_cal",
    "medi-cal": "medi_cal",
    "medicaid": "medi_cal",
    "chg": "medi_cal",
    "uninsured": "uninsured",
    "none": "uninsured",
    "cash": "uninsured",
}

MSG = {
    "stopped": {
        "en": "You're unsubscribed and won't get more messages. Text START to come back.",
        "es": "Cancelaste tu suscripción y no recibirás más mensajes. Escribe START para volver.",
    },
    "started": {
        "en": "Welcome back. Text me any health question.",
        "es": "Bienvenido de nuevo. Escríbeme cualquier pregunta de salud.",
    },
    "help": {
        "en": (
            "Care Navigator: text any health question and I'll tell you what to do, where to go nearby, "
            "and what it will cost with your coverage. Text STOP to unsubscribe. "
        ),
        "es": (
            "Care Navigator: escribe cualquier pregunta de salud y te digo qué hacer, a dónde ir cerca "
            "y cuánto costará con tu cobertura. Escribe STOP para cancelar. "
        ),
    },
    "profile_set": {
        "en": "Switched to your {label} profile ({desc}).",
        "es": "Cambiado a tu perfil {label} ({desc}).",
    },
    "profile_list": {
        "en": "Profiles: {labels}. Active: {active}. Switch with #profile kaiser, #profile medi_cal, or #profile uninsured.",
        "es": "Perfiles: {labels}. Activo: {active}. Cambia con #profile kaiser, #profile medi_cal o #profile uninsured.",
    },
    "profile_unknown": {
        "en": "I don't know that profile. Try #profile kaiser, #profile medi_cal, or #profile uninsured.",
        "es": "No conozco ese perfil. Prueba #profile kaiser, #profile medi_cal o #profile uninsured.",
    },
    "lang_set": {"en": "Okay, I'll reply in English.", "es": "Listo, te responderé en español."},
    "lang_unknown": {"en": "Try #lang en or #lang es.", "es": "Prueba #lang en o #lang es."},
    "reset": {
        "en": "Cleared. Text me any health question.",
        "es": "Listo, borrado. Escríbeme cualquier pregunta de salud.",
    },
    "reset_all": {
        "en": "Everything cleared, including your profiles. Text anything to start over.",
        "es": "Todo borrado, incluidos tus perfiles. Escribe cualquier cosa para empezar de nuevo.",
    },
    "whoami": {
        "en": "Active profile: {label}. Coverage: {desc}. Household: {household}. Income: {income}. Language: {lang}.",
        "es": "Perfil activo: {label}. Cobertura: {desc}. Hogar: {household}. Ingresos: {income}. Idioma: {lang}.",
    },
    "no_profile": {
        "en": "No profile yet. Text #profile kaiser, #profile medi_cal, or #profile uninsured.",
        "es": "Aún no hay perfil. Escribe #profile kaiser, #profile medi_cal o #profile uninsured.",
    },
    "followup_unavailable": {
        "en": "Follow-ups aren't ready yet.",
        "es": "Los seguimientos aún no están listos.",
    },
    "unknown_command": {
        "en": "Commands: #profile, #lang, #followup, #reset, #whoami.",
        "es": "Comandos: #profile, #lang, #followup, #reset, #whoami.",
    },
}


FRIENDLY = {
    "kaiser": {"en": "Kaiser Permanente, Covered California Silver", "es": "Kaiser Permanente, Covered California Silver"},
    "medi_cal": {"en": "Medi-Cal through Community Health Group", "es": "Medi-Cal con Community Health Group"},
    "uninsured": {"en": "no insurance, lower income", "es": "sin seguro, ingresos bajos"},
}

# Commands also work without the "#" (people drop it when typing on a phone), but only
# when the whole message is the command, so "is there a language barrier" stays a question.
_COMMAND = re.compile(
    r"^(#)?\s*(profile|whoami|who\s+am\s+i|lang|language|idioma|reset|follow\s*up)\b\s*(.*)$",
    re.IGNORECASE,
)


def parse_command(text: str) -> tuple[str, str] | None:
    """Return (command, arg) for '#profile kaiser', 'profile kaiser', 'who am i', etc."""
    m = _COMMAND.match(text.strip().rstrip(".!?"))
    if not m:
        return None
    hashed = bool(m.group(1))
    cmd = re.sub(r"\s+", "", m.group(2).lower())  # "who am i" -> "whoami", "follow up" -> "followup"
    if cmd in ("language", "idioma"):
        cmd = "lang"
    rest = m.group(3).split()
    arg = rest[0].lower() if rest else ""
    if not hashed:
        if len(rest) > 1:
            return None
        if cmd == "profile" and arg and arg not in PROFILE_ALIASES:
            return None
        if cmd == "lang" and arg not in ("en", "es"):
            return None
        if cmd == "reset" and arg not in ("", "all"):
            return None
        if cmd in ("whoami", "followup") and arg:
            return None
    return cmd, arg


def msg(key: str, language: str, **kwargs) -> str:
    lang = "es" if language == "es" else "en"
    return MSG[key][lang].format(**kwargs)


def describe(profile) -> str:
    plan = profile.plan_id or "no plan"
    insurer = profile.insurer or profile.coverage_type.value.replace("_", " ")
    return f"{insurer}, {plan}" if profile.plan_id else insurer


def _word(text: str) -> str:
    return text.strip().strip(".!").lower()


def is_stop(text: str) -> bool:
    return _word(text) in STOP_WORDS


def is_start(text: str) -> bool:
    return _word(text) in START_WORDS


def help_reply(language: str) -> str:
    lang = "es" if language == "es" else "en"
    return msg("help", lang) + NOT_FOR_EMERGENCIES[lang]


def handle_command(user: User, text: str) -> list[str] | None:
    """Return replies if the text is STOP / START / HELP or a #command, else None."""
    word = _word(text)
    lang = user.language

    if word in STOP_WORDS:
        store.set_opted_out(user.id, True)
        return [msg("stopped", lang)]
    if word in START_WORDS and user.opted_out:
        store.set_opted_out(user.id, False)
        return [msg("started", lang)]
    if word in HELP_WORDS:
        return [help_reply(lang)]

    parsed = parse_command(text)
    if parsed is None:
        if text.strip().startswith("#"):
            return [msg("unknown_command", lang)]
        return None
    cmd, arg = parsed

    if cmd == "profile":
        if not arg:
            profiles = store.list_profiles(user.id)
            active = next((p.label for p in profiles if p.id == user.active_profile_id), "none")
            labels = ", ".join(p.label for p in profiles) or "none"
            return [msg("profile_list", lang, labels=labels, active=active)]
        label = PROFILE_ALIASES.get(arg)
        if not label:
            return [msg("profile_unknown", lang)]
        profile = store.ensure_demo_profile(user, label)
        store.set_conversation(user.id, "idle", {})
        return [msg("profile_set", lang, label=label, desc=FRIENDLY[label]["es" if lang == "es" else "en"])]

    if cmd == "lang":
        if arg not in ("en", "es"):
            return [msg("lang_unknown", lang)]
        store.set_language(user.id, arg)
        return [msg("lang_set", arg)]

    if cmd == "reset":
        if arg == "all":
            store.delete_profiles(user.id)
            store.set_conversation(user.id, "new", {})
            return [msg("reset_all", lang)]
        store.set_conversation(user.id, "idle" if user.active_profile_id else "new", {})
        return [msg("reset", lang)]

    if cmd == "whoami":
        profile = store.get_active_profile(user)
        if not profile:
            return [msg("no_profile", lang)]
        income = profile.income_range_fpl.value + "% FPL" if profile.income_range_fpl else "unknown"
        return [
            msg(
                "whoami", lang,
                label=profile.label, desc=describe(profile),
                household=profile.household_size or "unknown", income=income, lang=user.language,
            )
        ]

    if cmd == "followup":
        try:
            from app.followup.scheduler import followup_now

            return followup_now(user.id)
        except NotImplementedError:
            return [msg("followup_unavailable", lang)]

    return [msg("unknown_command", lang)]
