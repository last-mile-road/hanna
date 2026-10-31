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

# MinerU needs torch and OpenCV's system libraries. T4 is compute 7.5, which PyTorch
# supports; PaddleOCR-VL does not (it needs >= 8.5), hence a separate image on an L4.
image_mineru = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0", "libgomp1", "poppler-utils")
    .pip_install("mineru[full]>=4.0,<5", "fastapi[standard]", "pillow")
    .env({"MINERU_HOME": "/models/mineru"})
)

# One shared secret for the VPS -> Modal hop. Created with:
#   modal secret create hanna-shared-key HANNA_KEY=<value>
shared_key = modal.Secret.from_name("hanna-shared-key")


@app.function(
    image=image_mineru,
    gpu="L4",
    max_containers=1,
    timeout=1800,
    volumes={"/models": models},
)
def parse_mineru(image_bytes: bytes, tier: str = "standard") -> dict:
    """One image in, MinerU's markdown and HTML tables out. Nothing is stored.

    Uses `mineru-kit parse`, which parses in-process. The `mineru parse` CLI needs a local
    doclib daemon, and that daemon binds a Unix socket inside MINERU_HOME — impossible on a
    Modal Volume (OSError 95). Home therefore lives on the container's /tmp, with the
    weights reached through a symlink to the Volume.
    """
    import os
    import subprocess
    import tempfile
    import time
    from pathlib import Path as _Path

    started = time.time()
    home = _Path("/tmp/mineru")
    home.mkdir(parents=True, exist_ok=True)
    persist = _Path("/models/mineru")
    (persist / "models").mkdir(parents=True, exist_ok=True)
    if not (home / "models").exists():
        (home / "models").symlink_to(persist / "models")
    env = {**os.environ, "MINERU_HOME": str(home)}
    log: list[str] = []

    def run(cmd, timeout=1500):
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        log.append(f"{' '.join(cmd)} -> {result.returncode} "
                   f"{(result.stderr or result.stdout or '')[-200:].strip()}")
        return result

    # The engine selector prefers vLLM only when it is installed; mineru[full] brings it.
    # Without this the VLM step falls back to llama.cpp on CPU and a card takes ~9 minutes.
    config_path = home / "config.yaml"
    if "engine: vllm" not in (config_path.read_text() if config_path.exists() else ""):
        config_path.write_text("model:\n  vlm:\n    engine: vllm\n")

    # Idempotent: the model set lives on the Volume, so only the first cold call pays.
    run(["mineru-kit", "models", "download", "--tier", tier], timeout=1500)

    with tempfile.TemporaryDirectory() as tmp:
        source = _Path(tmp) / "card.jpg"
        source.write_bytes(image_bytes)
        out = _Path(tmp) / "out"
        parsed = run(["mineru-kit", "parse", str(source), "--output", str(out),
                      "--tier", tier])
        markdown = ""
        for path in sorted(out.rglob("*.md")):
            markdown += path.read_text(errors="ignore")
        return {
            "reader": "mineru",
            "tier": tier,
            "seconds": round(time.time() - started, 1),
            "ok": parsed.returncode == 0 and bool(markdown),
            "markdown": markdown[:200000],
            "files": [str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()][:20],
            "setup_log": log[:6],
            "stored": False,
        }


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


@web.post("/parse/mineru")
async def parse_mineru_route(
    file: UploadFile = File(...),
    tier: str = Query("standard"),
    x_hanna_key: str | None = Header(default=None),
):
    _authorise(x_hanna_key)
    data = await file.read()
    return await parse_mineru.remote.aio(data, tier)


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
