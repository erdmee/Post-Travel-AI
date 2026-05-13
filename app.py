"""Modal entrypoint for Post-Travel-AI.

Deploys the FastAPI app onto a Modal GPU container so the same process
serves HTTP, runs CLIP on GPU, and calls Gemini.

Usage:
    modal serve app.py     # ephemeral dev URL with hot reload
    modal deploy app.py    # production URL
"""

from __future__ import annotations

import modal

APP_NAME = "post-travel-ai"
SECRET_NAME = "post-travel-ai-secrets"

LOCAL_SOURCE_DIRS = ["classifier", "blog", "server"]


def _download_clip_weights() -> None:
    """Run at image build time so weights are baked into the container.

    Without this, every cold start would re-download ~350MB from HuggingFace.
    """
    import open_clip

    open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    open_clip.get_tokenizer("ViT-B-32")


image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libheif-dev")
    .pip_install_from_requirements("requirements.txt")
    .run_function(_download_clip_weights)
    .add_local_python_source(*LOCAL_SOURCE_DIRS)
)

app = modal.App(APP_NAME)


@app.cls(
    image=image,
    gpu="T4",
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    scaledown_window=600,
    min_containers=0,
    max_containers=2,
    timeout=600,
)
@modal.concurrent(max_inputs=4)
class PostTravelAI:
    @modal.enter()
    def load_models(self) -> None:
        from classifier.classify import _load

        _load()

    @modal.asgi_app()
    def fastapi_app(self):
        from server.main import app as fastapi_app

        return fastapi_app
