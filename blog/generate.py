from __future__ import annotations

from .dedup import select_representatives
from .llm import call_llm
from .prompt import DEFAULT_PERSONA
from .types import BlogDraft, PhotoInput


def generate_blog_from_photos(
    photos: list[PhotoInput],
    persona: str = DEFAULT_PERSONA,
    target_count: int = 10,
) -> BlogDraft:
    if not photos:
        raise ValueError("photos must be non-empty")

    representatives = select_representatives(photos, target=target_count)
    if not representatives:
        raise ValueError("No representatives selected")

    return call_llm(representatives, persona=persona)
