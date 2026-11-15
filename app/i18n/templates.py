"""English and Spanish strings. Keys shared by onboarding, render, followup, safety."""

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {},
    "es": {},
}


def t(key: str, language: str, **kwargs) -> str:
    raise NotImplementedError
