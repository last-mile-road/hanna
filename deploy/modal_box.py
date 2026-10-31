"""The HANNA box: one L40S, one container, two models behind one proxy.

Why one container: Modal schedules a function per container, so two functions never share a
GPU. One container gives one GPU acquisition and one weight load for both models, which is
the whole cost of a cold start.

Layout (48 GB, fp16):

    port 8000   vLLM  Qwen3.5-9B                  gpu-memory-utilization 0.55
    port 8001   vLLM  PaddleOCR-VL-1.6            gpu-memory-utilization 0.18
    port 8080   FastAPI proxy, the only public port

    /llm/v1/...      -> 8000   (the pipeline's OpenAI client points here)
    /parse/paddle    -> doc_parser against 8001 (tables, layout)
    /warm            -> 202 until both engines answer, then 200
    /healthz         -> readiness of both

Task routing: tables (nerve, disability, treatment grid) to Paddle; text, diagrams and the
tick strips to the 9B. Two processes share the GPU by time-slicing, so they overlap rather
than double throughput — fine at our volume, and it removes a second cold start.

scaledown_window is 600 s: the box stays warm for ten minutes after the last call, which
covers a testing session without paying for a night.
"""

import os
import subprocess
import time

import modal

app = modal.App("hanna-box")

models = modal.Volume.from_name("hanna-models", create_if_missing=True)
shared_key = modal.Secret.from_name("hanna-shared-key")

BOX_IMAGE = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("libgl1", "libglib2.0-0", "libgomp1")
    .pip_install("vllm>=0.11.0", "paddleocr[doc-parser]>=3.4", "fastapi[standard]",
                 "httpx", "huggingface_hub[hf_transfer]", "safetensors")
    .pip_install("paddlepaddle-gpu==3.2.1",
                 index_url="https://www.paddlepaddle.org.cn/packages/stable/cu126/")
    .env({
        "HF_HOME": "/models/hf",
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "PADDLE_PDX_CACHE_HOME": "/models/paddlex",
        "PADDLEOCR_HOME": "/models/paddleocr",
    })
)

LLM_MODEL = os.environ.get("HANNA_LLM_MODEL", "Qwen/Qwen3.5-9B")
VLM_MODEL = "PaddlePaddle/PaddleOCR-VL-1.6"


@app.function(
    image=BOX_IMAGE,
    gpu="L40S",
    volumes={"/models": models},
    secrets=[shared_key],
    max_containers=1,
    timeout=24 * 3600,
    scaledown_window=600,
)
@modal.web_server(port=8080, startup_timeout=3600)
def box():
    """Start both vLLM servers and the proxy. Modal waits for 8080 to answer."""
    common = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    subprocess.Popen([
        "vllm", "serve", LLM_MODEL,
        "--host", "127.0.0.1", "--port", "8000",
        "--served-model-name", "hanna-llm",
        "--max-model-len", "8192",
        "--gpu-memory-utilization", "0.55",
        "--limit-mm-per-prompt", '{"image": 2}',
        "--disable-log-requests",
    ], **common)
    subprocess.Popen([
        "vllm", "serve", VLM_MODEL,
        "--host", "127.0.0.1", "--port", "8001",
        "--trust-remote-code",
        "--served-model-name", VLM_MODEL,
        "--max-num-batched-tokens", "16384",
        "--no-enable-prefix-caching",
        "--mm-processor-cache-gb", "0",
        "--gpu-memory-utilization", "0.18",
        "--disable-log-requests",
    ], **common)
    # Run the proxy in this process. Spawning `uvicorn deploy.modal_box:web` cannot work:
    # Modal mounts the app file on its own, so the module is not importable by that path.
    import uvicorn

    uvicorn.run(web, host="0.0.0.0", port=8080, log_level="info")


# --- the proxy lives in the same module so the container can import it ---------------

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

import httpx  # noqa: E402

web = FastAPI(title="HANNA box")


def _authorise(key):
    expected = os.environ.get("HANNA_KEY")
    if not expected or key != expected:
        raise HTTPException(401, "chave inválida")


async def _ready(port: int) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"http://127.0.0.1:{port}/v1/models")
            return response.status_code == 200
    except Exception:
        return False


@web.get("/healthz")
async def healthz():
    return {"ok": True, "llm": await _ready(8000), "paddle": await _ready(8001)}


@web.get("/warm")
async def warm(x_hanna_key: str = Header(default=None)):
    """Called by the client when a user opens the capture page, to hide the spin-up."""
    _authorise(x_hanna_key)
    llm, paddle = await _ready(8000), await _ready(8001)
    if llm and paddle:
        return {"ready": True}
    return JSONResponse({"ready": False, "llm": llm, "paddle": paddle}, status_code=202)


@web.api_route("/llm/{path:path}", methods=["GET", "POST"])
async def llm_proxy(path: str, request: Request, x_hanna_key: str = Header(default=None)):
    _authorise(x_hanna_key)
    if not await _ready(8000):
        raise HTTPException(503, "modelo ainda a carregar")
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() in {"content-type", "authorization", "accept"}}
    async with httpx.AsyncClient(timeout=600) as client:
        upstream = await client.request(
            request.method, f"http://127.0.0.1:8000/v1/{path}",
            content=body, headers=headers, params=dict(request.query_params),
        )
    return Response(upstream.content, status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type"))


@web.post("/parse/paddle")
async def parse_paddle_route(file: UploadFile = File(...),
                             x_hanna_key: str = Header(default=None)):
    _authorise(x_hanna_key)
    if not await _ready(8001):
        raise HTTPException(503, "PaddleOCR-VL ainda a carregar")
    import tempfile
    from pathlib import Path

    from paddleocr import PaddleOCRVL

    data = await file.read()
    started = time.time()
    markdown = ""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "card.jpg"
        source.write_bytes(data)
        out = Path(tmp) / "out"
        out.mkdir()
        pipeline = PaddleOCRVL(
            pipeline_version="v1.6",
            vl_rec_backend="vllm-server",
            vl_rec_server_url="http://127.0.0.1:8001/v1",
            vl_rec_api_model_name=VLM_MODEL,
        )
        for result in pipeline.predict(str(source)):
            result.save_to_markdown(save_path=str(out))
            result.save_to_json(save_path=str(out))
        for path in sorted(out.rglob("*.md")):
            markdown += path.read_text(errors="ignore")
    return {"reader": "paddleocr-vl", "seconds": round(time.time() - started, 1),
            "ok": bool(markdown), "markdown": markdown[:200000], "stored": False}
