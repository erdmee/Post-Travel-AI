from typing import NotRequired, TypedDict


class PhotoInput(TypedDict):
    photo_id: str
    image_bytes: bytes
    taken_at: NotRequired[str | None]
    lat: NotRequired[float | None]
    lng: NotRequired[float | None]
    scene_label: NotRequired[str | None]


class BlogSection(TypedDict):
    photo_ids: list[str]
    text: str


class BlogDraft(TypedDict):
    title: str
    summary: str
    sections: list[BlogSection]
