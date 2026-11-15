"""Thin Gemini Flash wrapper (google-genai SDK). Owned by #4.

    generate_json(prompt, schema, image=None, system=None) -> dict
    generate_text(prompt, system=None) -> str

* Model: GEMINI_MODEL, then one retry on GEMINI_FALLBACK_MODEL (default gemini-3.5-flash).
  Measured Nov 15 00:30 PT: gemini-flash-latest, 3.7 and 3.8 Flash timed out at 30 s (503
  "high demand"); gemini-3.5-flash answered in 5 to 8 s; gemini-3.5-flash-lite in under 1 s.
  Recommended .env: GEMINI_MODEL=gemini-3.5-flash-lite. Retrying the same overloaded model
  does not help, so the retry goes to the other model.
* Called on the v1beta API: the "-latest" aliases are not served on v1.
* `schema` is a plain JSON Schema dict ({"type": "object", "properties": ...}). Gemini is
  constrained to it, and the parsed dict is returned.
* Every attempt has a hard timeout and the SDK's own retries are off (they stacked into
  multi-minute hangs on 503s). Failures raise GeminiError, so callers can fall back to a
  fixed reply instead of crashing the webhook.
"""
from __future__ import annotations

import json
import logging
import os
import time

from google import genai
from google.genai import types

from app.config import settings

log = logging.getLogger(__name__)

TIMEOUT_S = 12
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")


class GeminiError(RuntimeError):
    """Gemini failed after retrying, or returned something unusable."""


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise GeminiError("GEMINI_API_KEY is not set")
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(
                api_version="v1beta",
                timeout=TIMEOUT_S * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
    return _client


def _image_mime(data: bytes) -> str:
    """MIME type for the `image` argument: photos, plus PDFs (plan benefit summaries, #14)."""
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "image/heic"  # iPhone photos
    return "image/jpeg"


def _config(system: str | None, schema: dict | None) -> types.GenerateContentConfig:
    kwargs: dict = {
        "system_instruction": system,
        "temperature": 0.2,
    }
    if schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_json_schema"] = schema
    return types.GenerateContentConfig(**kwargs)


def _models() -> list[str]:
    return list(dict.fromkeys([settings.gemini_model, FALLBACK_MODEL]))  # dedupe, keep order


def _generate(contents: list, config: types.GenerateContentConfig) -> str:
    last: Exception | None = None
    for model in _models():
        start = time.monotonic()
        try:
            resp = _get_client().models.generate_content(model=model, contents=contents, config=config)
            text = resp.text
            if not text:
                raise GeminiError(f"empty response (finish_reason={_finish_reason(resp)})")
            log.info("gemini %s ok in %.2fs", model, time.monotonic() - start)
            return text
        except Exception as e:  # SDK raises several error types; try the next model
            last = e
            log.warning("gemini %s failed after %.2fs: %s", model, time.monotonic() - start, e)
    raise GeminiError(str(last)) from last


def _finish_reason(resp) -> str | None:
    try:
        return str(resp.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return None


def generate_json(prompt: str, schema: dict, image: bytes | None = None, system: str | None = None) -> dict:
    contents: list = []
    if image is not None:
        contents.append(types.Part.from_bytes(data=image, mime_type=_image_mime(image)))
    contents.append(prompt)
    text = _generate(contents, _config(system, schema))
    try:
        out = json.loads(text)
    except json.JSONDecodeError as e:
        raise GeminiError(f"response was not JSON: {text[:200]}") from e
    if not isinstance(out, dict):
        raise GeminiError(f"expected a JSON object, got {type(out).__name__}")
    return out


def generate_text(prompt: str, system: str | None = None) -> str:
    return _generate([prompt], _config(system, None)).strip()
