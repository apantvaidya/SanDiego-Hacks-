"""Hard-coded red-flag screen. Runs before ANY LLM call. No network, no LLM.

Categories: cardiac, breathing, stroke, bleeding, self_harm, overdose, unconscious,
severe_allergy, pregnancy_emergency, head_injury. Include typos and Spanish.
Replies are fixed strings. self_harm reply includes 988.
"""
from app.models import SafetyResult


def screen(text: str, language: str = "en") -> SafetyResult:
    raise NotImplementedError
