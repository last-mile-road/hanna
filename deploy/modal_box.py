"""HANNA box v3: one L40S, three readers behind one authenticated proxy.

What changed from v2 (2026-10-31, which never answered):

  1. VLLM_USE_FLASHINFER_SAMPLER=0 in the image. vLLM's default sampling path
     JIT-builds a FlashInfer kernel with nvcc and that build fails; the same failure
     killed the 9B standalone until 18:09 that day. Every engine inherits it.
  2. The 9B gets 32k context and max_pixels 2M (blocks are crops; 4M was a full-card
     setting and it spends KV cache the readers need).
  3. Engine output is kept: each subprocess is piped to a 200-line ring buffer and
     echoed to the Modal log, and /ready returns the tail. v2 sent both engines to
     DEVNULL, so a dead engine looked like a 503 with no cause.
  4. Ordered GPU fallback: L40S, then RTX-PRO-6000, then A100-80GB. All 48 GB or more.
     A 40 GB card is deliberately absent: landing there silently would halve the KV
     cache and read as a model regression rather than a capacity event.
  5. max_inputs=8 on the web server, so one card's table read does not block another
     card's VLM call. The two reader clients are not reentrant and sit behind one
     asyncio lock; the 9B is left to vLLM's continuous batching.

Layout (48 GB, fp16):

    :8000  vLLM  Qwen3.5-9B               util 0.55   9B text, dips, block crops
    :8001  vLLM  PaddleOCR-VL-1.6         util 0.10   treatment grid, disability table
    :8002  mineru-api --tier standard      util ~0.13  tables, tick columns
    :8080  proxy: auth, /warm, /ready, /healthz, /llm/*, /parse/*

    Only 8080 is exposed. scaledown_window 300 s: warm for five minutes after the
    last request, then back to zero. timeout 2 h is a cost backstop on a forgotten
    container, not a request limit.

Deploy:
    MODAL_PROFILE=hanna ~/.local/share/hanna/modal-venv/bin/modal deploy deploy/modal_box.py

Smoke (one card, all three readers, waits for boot):
    python3 deploy/box_smoke.py --card ../hanna_archive/versions/v4_hanna3_2025-2026/test_forms/form16.jpg

Nothing is stored: images live in the request and in /tmp for the duration of the call.
Weights are on the hanna-models volume, which is not patient data.
"""

import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import modal

app = modal.App("hanna-box")

models = modal.Volume.from_name("hanna-models", create_if_missing=True)
shared_key = modal.Secret.from_name("hanna-shared-key")
vllm_key = modal.Secret.from_name("hanna-vllm-key")

LLM_MODEL = "Qwen/Qwen3.5-9B"
LLM_SERVED = "hanna-9b"
PADDLE_MODEL = "PaddlePaddle/PaddleOCR-VL-1.6"
MINERU_TIER = "standard"

IMAGE = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("libgl1", "libglib2.0-0", "libgomp1", "poppler-utils")
    .pip_install("vllm>=0.11.0", "fastapi[standard]", "httpx",
                 "huggingface_hub[hf_transfer]", "safetensors")
    .pip_install("mineru[full]>=4.0,<5")
    .pip_install("paddleocr[doc-parser]>=3.4")
    .pip_install("paddlepaddle-gpu==3.2.1",
                 index_url="https://www.paddlepaddle.org.cn/packages/stable/cu126/")
    .env({
        "HF_HOME": "/models/hf",
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "PADDLE_PDX_CACHE_HOME": "/models/paddlex",
        "PADDLEOCR_HOME": "/models/paddleocr",
        # The fix that cost a day on the 9B. Inherited by every child process,
        # including MinerU's in-process vLLM engine.
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
    })
)

PORTS = {"llm": 8000, "paddle": 8001, "mineru": 8002}
LOGS = {name: deque(maxlen=200) for name in PORTS}
PROCS: dict[str, subprocess.Popen] = {}
READY_AT: dict[str, float | None] = {name: None for name in PORTS}
BOOTED_AT: float | None = None


# --- process handling ---------------------------------------------------------------

def _pump(name: str, proc: subprocess.Popen) -> None:
    """Copy a child's output to the Modal log and to a bounded in-memory tail."""
    for line in proc.stdout:
        text = line.rstrip()
        if not text:
            continue
        LOGS[name].append(text[:400])
        print(f"[{name}] {text[:400]}", flush=True)


def _start(name: str, cmd: list[str], env: dict | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=env or os.environ.copy(),
    )
    PROCS[name] = proc
    threading.Thread(target=_pump, args=(name, proc), daemon=True).start()
    return proc


def _mineru_home() -> Path:
    """MinerU wants a writable home; a Modal Volume cannot host a Unix socket.

    The weights stay on the Volume and are reached through a symlink, so a cold
    container does not re-download them.
    """
    home = Path("/tmp/mineru")
    home.mkdir(parents=True, exist_ok=True)
    persist = Path("/models/mineru")
    (persist / "models").mkdir(parents=True, exist_ok=True)
    (home / "uploads").mkdir(parents=True, exist_ok=True)
    if not (home / "models").exists():
        (home / "models").symlink_to(persist / "models")
    return home


def _gpu_line() -> str:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return f"unavailable: {exc}"


def _probe(name: str) -> bool:
    """Readiness per engine, independent of the proxy."""
    import urllib.request

    url = f"http://127.0.0.1:{PORTS[name]}{'/health' if name == 'mineru' else '/v1/models'}"
    req = urllib.request.Request(url)
    key = os.environ.get("VLLM_API_KEY", "")
    if key and name == "llm":
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - not ready yet is the normal case
        return False


# --- the container ------------------------------------------------------------------

@app.function(
    image=IMAGE,
    gpu=["L40S", "RTX-PRO-6000", "A100-80GB"],
    cpu=8,
    memory=32768,
    volumes={"/models": models},
    secrets=[shared_key, vllm_key],
    max_containers=1,
    timeout=2 * 3600,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=8)
@modal.web_server(port=8080, startup_timeout=3600)
def box():
    global BOOTED_AT
    BOOTED_AT = time.time()

    print(f"[box] gpu at boot: {_gpu_line()}", flush=True)

    home = _mineru_home()
    env = {**os.environ, "MINERU_HOME": str(home)}

    # All three load in parallel: the slowest sets the cold start, and they are
    # already weight-cached, so the wait is engine init rather than network.
    _start("llm", [
        "vllm", "serve", LLM_MODEL,
        "--host", "127.0.0.1", "--port", str(PORTS["llm"]),
        "--api-key", os.environ.get("VLLM_API_KEY", ""),
        "--served-model-name", LLM_SERVED,
        "--max-model-len", "32768",
        "--gpu-memory-utilization", "0.55",
        "--limit-mm-per-prompt", '{"image": 2}',
        "--mm-processor-kwargs", '{"max_pixels": 2000000}',
        "--disable-log-requests",
    ])

    _start("paddle", [
        "vllm", "serve", PADDLE_MODEL,
        "--host", "127.0.0.1", "--port", str(PORTS["paddle"]),
        "--served-model-name", PADDLE_MODEL,
        "--trust-remote-code",
        "--max-num-batched-tokens", "16384",
        "--no-enable-prefix-caching",
        "--mm-processor-cache-gb", "0",
        "--gpu-memory-utilization", "0.10",
        "--disable-log-requests",
    ])

    _start("mineru", [
        "mineru-api",
        "--host", "127.0.0.1", "--port", str(PORTS["mineru"]),
        "--tier", MINERU_TIER, "--no-flash",
        "--preload-models",
        "--allow-local-source",
        "--concurrency", "1",
        "--retention-seconds", "0",
        "--upload-dir", str(home / "uploads"),
    ], env=env)

    import uvicorn

    # The proxy runs in this process. Spawning `uvicorn deploy.modal_box:web` cannot
    # work: Modal mounts the app file on its own, so the module is not importable there.
    uvicorn.run(web, host="0.0.0.0", port=8080, log_level="warning")


# --- the proxy ----------------------------------------------------------------------

import asyncio  # noqa: E402

import httpx  # noqa: E402
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

web = FastAPI(title="HANNA box")

_readers_lock: asyncio.Lock | None = None
_paddle_pipeline = None


def _lock() -> asyncio.Lock:
    global _readers_lock
    if _readers_lock is None:
        _readers_lock = asyncio.Lock()
    return _readers_lock


def _authorise(key: str | None, request: Request) -> None:
    """One secret at the edge: X-HANNA-Key, or a Bearer carrying the same value.

    The Bearer form exists so the OpenAI client can point straight at /llm/v1 with
    api_key=HANNA_KEY and send no custom headers.
    """
    expected = os.environ.get("HANNA_KEY", "")
    given = key
    if not given:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            given = header[7:]
    if not expected or given != expected:
        raise HTTPException(401, "chave inválida")


def _state() -> dict:
    now = time.time()
    engines = {}
    for name in PORTS:
        ok = _probe(name)
        if ok and READY_AT[name] is None:
            READY_AT[name] = round(now - (BOOTED_AT or now), 1)
        engines[name] = {
            "ready": ok,
            "port": PORTS[name],
            "ready_after_s": READY_AT[name],
            "tail": list(LOGS[name])[-15:],
        }
    return {
        "gpu": _gpu_line(),
        "elapsed_s": round(now - BOOTED_AT, 1) if BOOTED_AT else None,
        "ready": all(e["ready"] for e in engines.values()),
        "engines": engines,
    }


@web.get("/healthz")
async def healthz():
    """Liveness of the proxy only. Modal uses it to decide the container is up."""
    return {"ok": True, "box": "v3"}


@web.get("/warm")
async def warm(request: Request, x_hanna_key: str | None = Header(default=None)):
    """Called when a capture page opens, so the spin-up happens before the first card."""
    _authorise(x_hanna_key, request)
    state = _state()
    if state["ready"]:
        return {"ready": True, "elapsed_s": state["elapsed_s"], "gpu": state["gpu"]}
    return JSONResponse(
        {"ready": False,
         "engines": {k: v["ready"] for k, v in state["engines"].items()},
         "elapsed_s": state["elapsed_s"]},
        status_code=202,
    )


@web.get("/ready")
async def ready(request: Request, x_hanna_key: str | None = Header(default=None)):
    """Everything /warm hides: GPU model, per-engine ready time, last log lines."""
    _authorise(x_hanna_key, request)
    return _state()


@web.api_route("/llm/{path:path}", methods=["GET", "POST"])
async def llm_proxy(path: str, request: Request,
                    x_hanna_key: str | None = Header(default=None)):
    _authorise(x_hanna_key, request)
    if not _probe("llm"):
        raise HTTPException(503, "modelo ainda a carregar")
    body = await request.body()
    headers = {"Content-Type": request.headers.get("content-type", "application/json")}
    key = os.environ.get("VLLM_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=600) as client:
        upstream = await client.request(
            request.method, f"http://127.0.0.1:{PORTS['llm']}/v1/{path}",
            content=body, headers=headers, params=dict(request.query_params),
        )
    return Response(upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"))


def _run_paddle(image_bytes: bytes) -> dict:
    """PaddleOCR-VL against the local vLLM. Built once, reused; not reentrant."""
    global _paddle_pipeline
    import tempfile

    if _paddle_pipeline is None:
        from paddleocr import PaddleOCRVL

        _paddle_pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            vl_rec_backend="vllm-server",
            vl_rec_server_url=f"http://127.0.0.1:{PORTS['paddle']}/v1",
            vl_rec_api_model_name=PADDLE_MODEL,
        )

    started = time.time()
    markdown = ""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "card.jpg"
        source.write_bytes(image_bytes)
        out = Path(tmp) / "out"
        out.mkdir()
        for result in _paddle_pipeline.predict(str(source)):
            result.save_to_markdown(save_path=str(out))
            result.save_to_json(save_path=str(out))
        for path in sorted(out.rglob("*.md")):
            markdown += path.read_text(errors="ignore")
    return {"reader": "paddleocr-vl", "model": PADDLE_MODEL,
            "seconds": round(time.time() - started, 1), "ok": bool(markdown),
            "markdown": markdown[:200000], "stored": False}


def _run_mineru(image_bytes: bytes, tier: str) -> dict:
    """mineru-api, the same endpoint the local benchmark validated (/v1/file_parse)."""
    import json
    import tempfile
    import urllib.request

    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "card.jpg"
        source.write_bytes(image_bytes)
        payload = {
            "files": [{"source": {"type": "local", "path": str(source)}}],
            "tier": tier,
            "output_formats": ["markdown"],
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORTS['mineru']}/v1/file_parse?wait_timeout=900",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1200) as response:
            body = json.load(response)
        markdown = ""
        for entry in body.get("files", []):
            markdown += (entry.get("content") or {}).get("markdown") or ""
    return {"reader": "mineru", "tier": tier,
            "seconds": round(time.time() - started, 1), "ok": bool(markdown),
            "markdown": markdown[:200000], "stored": False}


@web.post("/parse/paddle")
async def parse_paddle_route(request: Request, file: UploadFile = File(...),
                             x_hanna_key: str | None = Header(default=None)):
    _authorise(x_hanna_key, request)
    if not _probe("paddle"):
        raise HTTPException(503, "PaddleOCR-VL ainda a carregar")
    data = await file.read()
    async with _lock():
        return await asyncio.to_thread(_run_paddle, data)


@web.post("/parse/mineru")
async def parse_mineru_route(request: Request, file: UploadFile = File(...),
                             tier: str = MINERU_TIER,
                             x_hanna_key: str | None = Header(default=None)):
    _authorise(x_hanna_key, request)
    if not _probe("mineru"):
        raise HTTPException(503, "MinerU ainda a carregar")
    data = await file.read()
    async with _lock():
        return await asyncio.to_thread(_run_mineru, data, tier)
