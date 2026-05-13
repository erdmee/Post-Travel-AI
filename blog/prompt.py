from __future__ import annotations

import io
import logging
from typing import Any, TypedDict

from google.genai import types
from PIL import Image, ImageOps, UnidentifiedImageError

from .types import PhotoInput

logger = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 50_000_000


class Persona(TypedDict):
    system: str
    section_rule: str


PERSONAS: dict[str, Persona] = {
    "friendly_diary": {
        "system": (
            "당신은 친근하고 일기 같은 톤의 여행 블로그 작가입니다. "
            "친구에게 카톡으로 후기를 들려주듯 솔직하고 자연스럽게 쓰세요. "
            "감정을 직접 표현하고, 평어와 존댓말이 자연스럽게 섞여도 OK."
        ),
        "section_rule": "텍스트는 2~4문장의 자연스러운 일기체.",
    },
    "emotional_essay": {
        "system": (
            "당신은 서정적이고 묘사 중심의 여행 에세이 작가입니다. "
            "풍경의 색과 소리, 공기의 결, 마음의 떨림을 비유와 은유로 표현하세요. "
            "문장의 호흡은 길고 감각적입니다."
        ),
        "section_rule": "텍스트는 3~5문장의 서정적 묘사. 비유와 은유로 풍경·감정을 표현.",
    },
    "witty": {
        "system": (
            "당신은 재치 있고 솔직한 여행 블로거입니다. "
            "농담과 과장, 자기조롱을 섞어 의외성 있는 표현으로 쓰세요. "
            "독자를 한 번이라도 웃기는 게 목표. 너무 진지하면 안 됩니다."
        ),
        "section_rule": "텍스트는 1~3문장의 위트 있는 표현. 농담·과장·자기조롱 환영.",
    },
    "concise_log": {
        "system": (
            "당신은 간결한 여행 메모 작가입니다. "
            "정보와 팁 위주로 짧게 쓰세요. 형용사·부사는 최소화하고 명사 중심으로. "
            "감정 표현은 절제하고, 군더더기는 모두 제거."
        ),
        "section_rule": "텍스트는 1~2문장. 정보·팁·평가 중심. 명사 위주.",
    },
    "magazine": {
        "system": (
            "당신은 객관적이고 정보적인 여행 매거진 에디터입니다. "
            "3인칭 어조로 지역의 콘텍스트·역사·문화·로컬 음식 등을 곁들이세요. "
            "독자가 그 장소에 가지 않고도 이해할 수 있도록 정보를 제공합니다."
        ),
        "section_rule": "텍스트는 2~4문장의 매거진 톤. 3인칭 어조, 지역 콘텍스트·문화 정보 포함.",
    },
}

DEFAULT_PERSONA = "friendly_diary"

_LLM_IMAGE_MAX = 1024


def get_persona(name: str) -> Persona:
    if name not in PERSONAS:
        raise ValueError(
            f"Unknown persona: {name!r}. Available: {list(PERSONAS)}"
        )
    return PERSONAS[name]


def _to_jpeg_for_llm(image_bytes: bytes, max_size: int = _LLM_IMAGE_MAX) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    if max(img.size) > max_size:
        img.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _format_photo_meta(i: int, photo: PhotoInput) -> str:
    parts = [f"[사진 {i + 1}] photo_id: {photo['photo_id']}"]
    if photo.get("taken_at"):
        parts.append(f"시각: {photo['taken_at']}")
    lat, lng = photo.get("lat"), photo.get("lng")
    if lat is not None and lng is not None:
        parts.append(f"GPS: {lat:.4f}, {lng:.4f}")
    if photo.get("scene_label"):
        parts.append(f"라벨: {photo['scene_label']}")
    return " | ".join(parts)


def build_contents(
    photos: list[PhotoInput], persona: str = DEFAULT_PERSONA
) -> tuple[list[Any], list[PhotoInput]]:
    """Return (contents, valid_photos). Photos that fail to decode are skipped."""
    persona_data = get_persona(persona)

    valid_photos: list[PhotoInput] = []
    parts: list[Any] = []
    for photo in photos:
        try:
            jpeg_bytes = _to_jpeg_for_llm(photo["image_bytes"])
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
            logger.warning(
                "Skipping unreadable photo %s: %s", photo.get("photo_id"), e
            )
            continue
        parts.append(_format_photo_meta(len(valid_photos), photo))
        parts.append(types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"))
        valid_photos.append(photo)

    instructions = f"""아래는 시간순으로 정렬된 {len(valid_photos)}장의 여행 사진과 메타데이터입니다.

[구조 규칙]
- 각 섹션은 1~4장의 사진과 그 아래 짧은 텍스트로 구성됩니다.
- 같은 장면·같은 식사·같은 활동의 사진들은 한 섹션에 묶으세요.
- 모든 사진은 정확히 한 번씩만 등장해야 합니다 (어느 섹션의 photo_ids에든 포함).
- photo_id는 주어진 값을 정확히 그대로 사용하세요.

[스타일 규칙]
- {persona_data["section_rule"]}

응답은 다음 JSON 형식 (코드 펜스 없이):
{{
  "title": "여행지 느낌이 살아있는 한 줄 제목",
  "summary": "여행 전체 분위기 한 문단",
  "sections": [
    {{ "photo_ids": ["..."], "text": "..." }}
  ]
}}
"""
    contents: list[Any] = [instructions, *parts]
    return contents, valid_photos
