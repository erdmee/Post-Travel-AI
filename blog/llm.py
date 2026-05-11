from __future__ import annotations

import json
import logging
import os
from typing import cast

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .prompt import DEFAULT_PERSONA, build_contents, get_persona
from .types import BlogDraft, PhotoInput

load_dotenv()

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, ServerError) and exc.code in (429, 500, 502, 503, 504)


def _get_client() -> tuple[genai.Client, str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env (see .env.example)"
        )
    model = os.getenv("LLM_MODEL", "gemini-2.0-flash")
    return genai.Client(api_key=api_key), model


def _validate(data: dict, expected_ids: set[str]) -> BlogDraft:
    if not isinstance(data, dict):
        raise ValueError("Response is not a JSON object")
    for key in ("title", "summary", "sections"):
        if key not in data:
            raise ValueError(f"Missing key in response: {key}")

    sections = data["sections"]
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections must be a non-empty list")

    seen: set[str] = set()
    for s in sections:
        if not isinstance(s, dict) or "photo_ids" not in s or "text" not in s:
            raise ValueError(f"Invalid section structure: {s}")
        for pid in s["photo_ids"]:
            seen.add(pid)

    missing = expected_ids - seen
    if missing:
        raise ValueError(f"Photos not referenced in any section: {missing}")

    return cast(BlogDraft, data)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    before_sleep=lambda rs: logger.warning(
        "Gemini transient error (%s), retrying in %.1fs (attempt %d)",
        rs.outcome.exception(),
        rs.next_action.sleep,
        rs.attempt_number,
    ),
    reraise=True,
)
def _generate_with_retry(client, model, contents, config):
    return client.models.generate_content(
        model=model, contents=contents, config=config
    )


def call_llm(
    photos: list[PhotoInput], persona: str = DEFAULT_PERSONA
) -> BlogDraft:
    client, model = _get_client()
    persona_data = get_persona(persona)
    contents = build_contents(photos, persona)

    response = _generate_with_retry(
        client,
        model,
        contents,
        types.GenerateContentConfig(
            system_instruction=persona_data["system"],
            temperature=0.7,
            response_mime_type="application/json",
        ),
    )

    raw = response.text
    if not raw:
        raise RuntimeError("LLM returned empty content")

    data = json.loads(raw)
    expected_ids = {p["photo_id"] for p in photos}
    return _validate(data, expected_ids)
