"""Thin Gemini Flash wrapper (google-genai SDK)."""


def generate_json(prompt: str, schema: dict, image: bytes | None = None, system: str | None = None) -> dict:
    raise NotImplementedError


def generate_text(prompt: str, system: str | None = None) -> str:
    raise NotImplementedError
