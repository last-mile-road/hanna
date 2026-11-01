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

Two failures on the first boot (2026-11-01), both visible only because the logs are
kept now:

  - `torch` would not import: `undefined symbol: ncclCommWindowDeregister`. The GPU
    Paddle wheel installs nvidia-nccl-cu12 2.25.1 next to torch's cu13 NCCL, at the
    same site-packages path, and torch binds the older one. Paddle's layout stage
    therefore runs on the CPU wheel; the VLM stage still runs on the GPU through
    vLLM at :8001, so the accuracy-carrying half is unchanged.
  - MinerU 4.1.3 dropped `--no-flash` and `--allow-local-source`, and its API moved
    from local paths to a task-plus-upload protocol. The proxy now uses MinerU's own
    client (`mineru.parser.api_client.MinerUApiParser`), which is the supported path
    in 4.1.3 and avoids re-implementing the upload handshake.

Second boot: torch imported, MinerU came up on `vllm-async-engine` in 90 s, but both
own vLLM servers died on `unrecognized arguments: --disable-log-requests` — that flag
is gone from the vLLM CLI. The image had also resolved vLLM down to 0.22.1, because
`mineru[full]` carries its own vLLM constraint. MinerU is now installed with the
`[torch]` extra only, and vLLM is pinned last at 0.30.0, the version the 9B was
verified on: MinerU's engine selector picks up the installed vLLM instead of dictating
it, and all three engines share one version.


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
# The MinerU VLM weights are on the volume already, in HF layout.
MINERU_VLM_PATH = "/models/mineru/models/MinerU2.5-Pro-2605-1.2B"
MINERU_VLM_SERVED = "mineru-vlm"

IMAGE = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("libgl1", "libglib2.0-0", "libgomp1", "poppler-utils")
    .pip_install("vllm==0.30.0", "fastapi[standard]", "httpx",
                 "huggingface_hub[hf_transfer]", "safetensors")
    # [torch], not [full]: [full] brings its own vLLM constraint and dragged the
    # engine down to 0.22.1. MinerU's selector picks up whatever vLLM is installed.
    .pip_install("mineru[torch]>=4.0,<5")
    # CPU wheel on purpose: the GPU wheel drags in nvidia-nccl-cu12 2.25.1, which
    # shadows torch's cu13 NCCL and breaks `import torch` for every vLLM engine.
    # Paddle's layout stage on CPU costs a second or two per crop; its VLM still
    # runs on the GPU through vLLM at :8001.
    .pip_install("paddleocr[doc-parser]>=3.4", "paddlepaddle==3.2.1")
    # Last word on the engine version: every one of the three uses this vLLM.
    .pip_install("vllm==0.30.0")
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

PORTS = {"llm": 8000, "paddle": 8001, "mineru": 8002, "mineru_vlm": 8003}

# Paddle is off by default. On a 46 GiB L40S the 9B plus Paddle leave 5.7 GiB free,
# and Paddle's only unique win is one cell on the treatment grid (22/23 against the
# 9B's 21/23), while it is the weakest reader on the nerve grid (48/72 against
# MinerU's 84/84). Set HANNA_BOX_PADDLE=1 on a 96 GB card to bring it back.
PADDLE_ENABLED = os.environ.get("HANNA_BOX_PADDLE", "0") == "1"
ACTIVE = tuple(name for name in PORTS if name != "paddle" or PADDLE_ENABLED)
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
    # vLLM reads VLLM_API_KEY from the environment as well as --api-key, so a server
    # we meant to leave keyless will still answer 401. Send the key either way; a
    # server that does not want it ignores the header.
    key = os.environ.get("VLLM_API_KEY", "")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - not ready yet is the normal case
        return False


def _await(name: str, timeout: int) -> bool:
    """Wait in the boot thread until one engine answers, or it exits, or time runs out.

    An engine that exits is not worth waiting for; the next one starts anyway so the
    box comes up partially rather than not at all, and /ready shows which one is down.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _probe(name):
            print(f"[box] {name} ready at {round(time.time() - (BOOTED_AT or time.time()), 1)}s",
                  flush=True)
            return True
        if name in PROCS and PROCS[name].poll() is not None:
            print(f"[box] {name} exited with code {PROCS[name].returncode}; "
                  f"starting the next engine anyway", flush=True)
            return False
        time.sleep(5)
    print(f"[box] {name} not ready after {timeout}s", flush=True)
    return False


def _degraded(name: str) -> str:
    """mineru-api answers /health even when its VLM engine died at startup."""
    if name != "mineru":
        return ""
    if any("Engine core initialization failed" in line for line in LOGS[name]):
        return "VLM engine failed at startup; /health still answers but parsing will fail"
    return ""


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
@modal.asgi_app()
def box():
    global BOOTED_AT
    BOOTED_AT = time.time()

    print(f"[box] gpu at boot: {_gpu_line()}", flush=True)

    env = {**os.environ, "MINERU_HOME": str(_mineru_home())}

    # The engines start in sequence, in a background thread, so the proxy answers
    # /warm while they load. Sequence, not parallel: vLLM 0.30 reads
    # gpu_memory_utilization as a fraction of *free* memory at engine init, so three
    # engines racing for the card starve each other (0.10 of the memory left after the
    # 9B was not enough for Paddle's cache blocks). Starting each one against the
    # memory that is actually free makes the fractions meaningful.
    def boot():
        _start("llm", [
            "vllm", "serve", LLM_MODEL,
            "--host", "127.0.0.1", "--port", str(PORTS["llm"]),
            "--api-key", os.environ.get("VLLM_API_KEY", ""),
            "--served-model-name", LLM_SERVED,
            "--max-model-len", "32768",
            "--gpu-memory-utilization", "0.55",
            "--limit-mm-per-prompt", '{"image": 2}',
            "--mm-processor-kwargs", '{"max_pixels": 2000000}',
            # The default 4 GiB image cache is dead weight here: every request brings
            # a different crop, and that 4 GiB is a reader's worth of memory.
            "--mm-processor-cache-gb", "0",
        ])
        _await("llm", 900)

        # No VLLM_API_KEY in this process: vLLM would otherwise require a bearer on
        # the localhost port, and Paddle's client has no way to send one.
        paddle_env = {k: v for k, v in os.environ.items() if k != "VLLM_API_KEY"}
        if PADDLE_ENABLED:
            _start("paddle", [
                "vllm", "serve", PADDLE_MODEL,
                "--host", "127.0.0.1", "--port", str(PORTS["paddle"]),
                "--served-model-name", PADDLE_MODEL,
                "--trust-remote-code",
                "--max-num-batched-tokens", "8192",
                "--no-enable-prefix-caching",
                "--mm-processor-cache-gb", "0",
                "--gpu-memory-utilization", "0.30",
            ], env=paddle_env)
            _await("paddle", 300)

        # MinerU's own engine computes its budget as ~0.13 of whatever is free, and
        # that is not configurable — next to the 9B it reserves less than the model's
        # weights and dies at engine init. So its VLM runs as a fourth server we size
        # ourselves, and mineru-api talks to it over HTTP (--vlm-server-url), which is
        # the path MinerU's hosted service uses.
        keyless = {k: v for k, v in os.environ.items() if k != "VLLM_API_KEY"}
        _start("mineru_vlm", [
            "vllm", "serve", MINERU_VLM_PATH,
            "--host", "127.0.0.1", "--port", str(PORTS["mineru_vlm"]),
            "--served-model-name", MINERU_VLM_SERVED,
            "--max-model-len", "8192",
            "--gpu-memory-utilization", "0.50",
            "--mm-processor-cache-gb", "0",
            "--trust-remote-code",
        ], env=keyless)
        _await("mineru_vlm", 300)

        _start("mineru", [
            "mineru-api",
            "--host", "127.0.0.1", "--port", str(PORTS["mineru"]),
            "--tier", MINERU_TIER,
            "--preload-models",
            "--vlm-server-url", f"http://127.0.0.1:{PORTS['mineru_vlm']}/v1",
            "--vlm-model", MINERU_VLM_SERVED,
            "--concurrency", "1",
            "--retention-seconds", "0",
            "--upload-dir", "/tmp/mineru/uploads",
        ], env=env)
        _await("mineru", 600)

    threading.Thread(target=boot, daemon=True).start()

    print("[box] proxy ready, engines booting", flush=True)
    return web


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
    for name in ACTIVE:
        ok = _probe(name)
        problem = _degraded(name)
        if ok and not problem and READY_AT[name] is None:
            READY_AT[name] = round(now - (BOOTED_AT or now), 1)
        engines[name] = {
            "ready": ok and not problem,
            "port": PORTS[name],
            "ready_after_s": READY_AT[name],
            "problem": problem,
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
    """MinerU 4.1.3 through its own client: task, upload, poll, result zip.

    4.1.3 dropped `--allow-local-source` and moved /v1/file_parse onto a task
    protocol. MinerUApiParser is the supported way in, and it returns the rendered
    markdown from the same parser the local benchmark measured.
    """
    import tempfile

    from mineru.parser.api_client import MinerUApiParser

    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "card.jpg"
        source.write_bytes(image_bytes)
        parser = MinerUApiParser(api_url=f"http://127.0.0.1:{PORTS['mineru']}", tier=tier)
        markdown = parser.parse(source).markdown()
    return {"reader": "mineru", "tier": tier,
            "seconds": round(time.time() - started, 1), "ok": bool(markdown),
            "markdown": markdown[:200000], "stored": False}


@web.post("/parse/paddle")
async def parse_paddle_route(request: Request, file: UploadFile = File(...),
                             x_hanna_key: str | None = Header(default=None)):
    _authorise(x_hanna_key, request)
    if not PADDLE_ENABLED:
        raise HTTPException(503, "PaddleOCR-VL está desligado nesta configuração "
                                 "(HANNA_BOX_PADDLE=1 para ligar)")
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
