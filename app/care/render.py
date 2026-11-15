"""Render option cards from DB facts. The LLM never writes these facts."""
from app.models import Profile, RenderedOption


def render_options(options: list[RenderedOption], language: str) -> str:
    raise NotImplementedError


def render_fallback(profile: Profile, language: str) -> str:
    """No verified options: plan nurse/advice line if known, 211 San Diego, telehealth if covered."""
    raise NotImplementedError
