"""HANNA readers on Modal — CPU queue in front, one GPU container behind, nothing stored.

Design (2026-10-30):
  - the CPU web endpoint is the orchestrator: it accepts the image, hands it to the GPU
    function and waits. `max_containers=1` serialises the GPU side, so a burst queues
    instead of multiplying cold starts and cost;
  - no autoscaling above one GPU container until the latency numbers say otherwise;
  - the image exists only in memory and in the container's /tmp for the duration of the
    call. No Volume, no object storage, nothing survives the request;
  - model weights may live on a Volume later (`hanna-models`), because weights are not
    patient data and re-downloading them on every cold start is the expensive part.

Deploy:
    MODAL_PROFILE=hanna ~/.local/share/hanna/modal-venv/bin/modal deploy deploy/modal_app.py

Test:
    curl -s -F "file=@card.jpg" "https://<url>/parse?reader=skeleton"
"""

import hashlib

import modal

app = modal.App("hanna-readers")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]", "pillow")
)

# Weights, not cards. Safe to persist and expensive to fetch.
models = modal.Volume.from_name("hanna-models", create_if_missing=True)

# One shared secret for the VPS -> Modal hop. Created with:
#   modal secret create hanna-shared-key HANNA_KEY=<value>
shared_key = modal.Secret.from_name("hanna-shared-key")


@app.function(
    image=image,
    gpu="T4",
    max_containers=1,          # one line for now; no autoscale
    timeout=600,
    volumes={"/models": models},
)
def read_card(image_bytes: bytes, reader: str = "skeleton") -> dict:
    """One image in, one JSON out. Skeleton until the MinerU and Paddle images are built."""
    digest = hashlib.sha256(image_bytes).hexdigest()[:12]
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as picture:
        width, height = picture.size
    return {
        "reader": reader,
        "status": "skeleton",
        "bytes": len(image_bytes),
        "sha256_12": digest,
        "width": width,
        "height": height,
        "stored": False,
        "note": "replace with mineru/paddle parsing; the queue and the no-storage rule are in place",
    }


import os

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile

web = FastAPI(title="HANNA readers")


def _authorise(key: str | None) -> None:
    expected = os.environ.get("HANNA_KEY")
    if not expected or key != expected:
        raise HTTPException(401, "chave inválida")


@web.get("/healthz")
def healthz():
    return {"ok": True, "reader": "skeleton", "auth": True}


@web.post("/parse")
async def parse(
    file: UploadFile = File(...),
    reader: str = Query("skeleton"),
    x_hanna_key: str | None = Header(default=None),
):
    """Accept the upload, queue the single GPU container, return its JSON."""
    _authorise(x_hanna_key)
    data = await file.read()
    return await read_card.remote.aio(data, reader)


@app.function(image=image, secrets=[shared_key])
@modal.asgi_app()
def api():
    return web
