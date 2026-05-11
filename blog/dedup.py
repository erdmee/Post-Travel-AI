from __future__ import annotations

from datetime import datetime, timedelta

import torch

from classifier import embed_image

from .types import PhotoInput

DEFAULT_TIME_GAP = timedelta(minutes=30)
DEFAULT_SIM_THRESHOLD = 0.92


def _parse_time(t: str | None) -> datetime | None:
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_by_time(
    photos: list[PhotoInput], gap: timedelta
) -> list[list[PhotoInput]]:
    if not photos:
        return []
    events: list[list[PhotoInput]] = [[photos[0]]]
    for p in photos[1:]:
        prev_t = _parse_time(events[-1][-1].get("taken_at"))
        curr_t = _parse_time(p.get("taken_at"))
        if prev_t is None or curr_t is None or (curr_t - prev_t) > gap:
            events.append([p])
        else:
            events[-1].append(p)
    return events


def _greedy_dedup(embeddings: list[torch.Tensor], threshold: float) -> list[int]:
    kept: list[int] = []
    for i, emb in enumerate(embeddings):
        is_dup = False
        for j in kept:
            sim = float(torch.dot(emb, embeddings[j]))
            if sim > threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(i)
    return kept


def select_representatives(
    photos: list[PhotoInput],
    target: int = 10,
    time_gap: timedelta = DEFAULT_TIME_GAP,
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
) -> list[PhotoInput]:
    if not photos:
        return []

    sorted_photos = sorted(
        photos,
        key=lambda p: _parse_time(p.get("taken_at")) or datetime.min,
    )

    events = _split_by_time(sorted_photos, time_gap)

    representatives: list[PhotoInput] = []
    for event in events:
        embs = [embed_image(p["image_bytes"]) for p in event]
        for i in _greedy_dedup(embs, sim_threshold):
            representatives.append(event[i])

    if len(representatives) > target:
        step = len(representatives) / target
        indices = [int(i * step) for i in range(target)]
        representatives = [representatives[i] for i in indices]

    return representatives
