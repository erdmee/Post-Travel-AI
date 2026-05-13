from __future__ import annotations

import io
from typing import Literal

import open_clip
import torch
from PIL import Image
from pillow_heif import register_heif_opener

register_heif_opener()

Label = Literal["person", "landscape", "food", "architecture", "cityscape"]

_PROMPT_BY_LABEL: dict[Label, str] = {
    "person": "a photo of a person",
    "landscape": "a photo of a natural landscape",
    "food": "a photo of food",
    "architecture": "a photo of a building",
    "cityscape": "a photo of a city street",
}
_LABELS: tuple[Label, ...] = tuple(_PROMPT_BY_LABEL.keys())
_PROMPTS = list(_PROMPT_BY_LABEL.values())

_model = None
_preprocess = None
_text_features = None
_device = None


def _load() -> None:
    global _model, _preprocess, _text_features, _device
    if _model is not None:
        return

    if torch.backends.mps.is_available():
        _device = torch.device("mps")
    elif torch.cuda.is_available():
        _device = torch.device("cuda")
    else:
        _device = torch.device("cpu")

    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    model = model.to(_device).eval()
    tokenizer = open_clip.get_tokenizer("ViT-B-32")

    with torch.no_grad():
        text_tokens = tokenizer(_PROMPTS).to(_device)
        text_features = model.encode_text(text_tokens)
        text_features /= text_features.norm(dim=-1, keepdim=True)

    _model = model
    _preprocess = preprocess
    _text_features = text_features


def _encode(image_bytes: bytes) -> torch.Tensor:
    _load()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_tensor = _preprocess(image).unsqueeze(0).to(_device)
    with torch.no_grad():
        feat = _model.encode_image(image_tensor)
        feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat


def classify(image_bytes: bytes) -> Label:
    feat = _encode(image_bytes)
    with torch.no_grad():
        probs = (100.0 * feat @ _text_features.T).softmax(dim=-1)
    return _LABELS[int(probs.argmax().item())]


def embed_image(image_bytes: bytes) -> torch.Tensor:
    """L2-normalized 512-dim CLIP image embedding as a 1D CPU tensor."""
    return _encode(image_bytes).squeeze(0).detach().cpu()
