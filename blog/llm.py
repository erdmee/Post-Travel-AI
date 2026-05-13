from __future__ import annotations

import json
import logging
import os
from typing import cast

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    stop_any,
    wait_exponential,
)

from .prompt import DEFAULT_PERSONA, build_contents, get_persona
from .types import BlogDraft, PhotoInput

load_dotenv()

logger = logging.getLogger(__name__)


_LLM_CALL_TIMEOUT_MS = 90_000
_MAX_OUTPUT_TOKENS = 8192
_RETRY_MAX_ATTEMPTS = 3
_RETRY_MAX_TOTAL_SECONDS = 150


class SafetyBlockedError(RuntimeError):
    """LLM 응답이 safety filter로 차단됨."""


class _SectionModel(BaseModel):
    photo_ids: list[str]
    text: str


class _BlogResponseModel(BaseModel):
    title: str
    summary: str
    sections: list[_SectionModel]


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, SafetyBlockedError):
        return False
    if isinstance(exc, ServerError) and exc.code in (429, 500, 502, 503, 504):
        return True
    if isinstance(
        exc,
        (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError),
    ):
        return True
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return True
    return False


def _get_client() -> tuple[genai.Client, str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env (see .env.example)"
        )
    model = os.getenv("LLM_MODEL", "gemini-2.5-flash")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=_LLM_CALL_TIMEOUT_MS),
    )
    return client, model


def _safety_settings() -> list[types.SafetySetting]:
    return [
        types.SafetySetting(
            category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_ONLY_HIGH"
        ),
        types.SafetySetting(
            category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_ONLY_HIGH"
        ),
        types.SafetySetting(
            category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_ONLY_HIGH"
        ),
        types.SafetySetting(
            category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_ONLY_HIGH"
        ),
    ]


def _validate(data: dict, expected_ids: set[str]) -> BlogDraft:
    if not isinstance(data, dict):
        raise ValueError("Response is not a JSON object")
    for key in ("title", "summary", "sections"):
        if key not in data:
            raise ValueError(f"Missing key in response: {key}")

    title = data.get("title")
    summary = data.get("summary")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")

    sections = data["sections"]
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections must be a non-empty list")

    seen_order: list[str] = []
    for s in sections:
        if not isinstance(s, dict) or "photo_ids" not in s or "text" not in s:
            raise ValueError(f"Invalid section structure: {s}")
        text = s.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Section has empty text: {s}")
        pids = s.get("photo_ids")
        if not isinstance(pids, list) or not pids:
            raise ValueError(f"Section has empty or invalid photo_ids: {s}")
        if len(pids) > 4:
            raise ValueError(f"Section has more than 4 photo_ids: {s}")
        for pid in pids:
            if not isinstance(pid, str):
                raise ValueError(f"photo_id must be a string: {pid!r}")
            seen_order.append(pid)

    seen = set(seen_order)
    if len(seen_order) != len(seen):
        raise ValueError(
            f"Duplicate photo_ids across sections: "
            f"{[p for p in seen_order if seen_order.count(p) > 1]}"
        )
    extra = seen - expected_ids
    if extra:
        raise ValueError(f"Hallucinated photo_ids not in input: {extra}")
    missing = expected_ids - seen
    if missing:
        raise ValueError(f"Photos not referenced in any section: {missing}")

    return cast(BlogDraft, data)


def _extract_text(response: types.GenerateContentResponse) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError("LLM response has no candidates")

    candidate = candidates[0]
    finish = getattr(candidate, "finish_reason", None)
    finish_name = getattr(finish, "name", None) or str(finish or "UNKNOWN")

    if finish_name == "SAFETY":
        ratings = []
        for r in getattr(candidate, "safety_ratings", None) or []:
            cat = getattr(getattr(r, "category", None), "name", str(r))
            prob = getattr(getattr(r, "probability", None), "name", "?")
            ratings.append(f"{cat}={prob}")
        raise SafetyBlockedError(f"Response blocked by safety filter: {ratings}")

    if finish_name == "RECITATION":
        raise SafetyBlockedError("Response blocked due to recitation")

    if finish_name == "MAX_TOKENS":
        raise ValueError("Response truncated by max_output_tokens")

    if finish_name not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise ValueError(f"Unexpected finish_reason: {finish_name}")

    try:
        raw = response.text
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Failed to extract text from response: {e}") from e

    if not raw or not raw.strip():
        raise ValueError("LLM returned empty content")

    return raw


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_any(
        stop_after_attempt(_RETRY_MAX_ATTEMPTS),
        stop_after_delay(_RETRY_MAX_TOTAL_SECONDS),
    ),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    before_sleep=lambda rs: logger.warning(
        "LLM transient error (%r), retrying in %.1fs (attempt %d)",
        rs.outcome.exception() if rs.outcome else None,
        rs.next_action.sleep if rs.next_action else 0.0,
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
    contents, valid_photos = build_contents(photos, persona)
    if not valid_photos:
        raise ValueError("No readable photos to send to LLM")

    config = types.GenerateContentConfig(
        system_instruction=persona_data["system"],
        temperature=0.7,
        response_mime_type="application/json",
        response_schema=_BlogResponseModel,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        safety_settings=_safety_settings(),
    )

    response = _generate_with_retry(client, model, contents, config)

    raw = _extract_text(response)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[-1].rsplit("```", 1)[0]
            if cleaned.lstrip().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
        data = json.loads(cleaned)

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        logger.info(
            "gemini usage: prompt=%s candidates=%s total=%s",
            getattr(usage, "prompt_token_count", None),
            getattr(usage, "candidates_token_count", None),
            getattr(usage, "total_token_count", None),
        )

    expected_ids = {p["photo_id"] for p in valid_photos}
    return _validate(data, expected_ids)
