"""PaddleOCR-VL 1.6 on Modal: vLLM server + doc_parser client, one L4, fp16.

Paddle's supported deployment is two layers:

  vllm serve PaddlePaddle/PaddleOCR-VL-1.6          (the VLM, OpenAI-compatible)
  paddleocr doc_parser --vl_rec_backend vllm-server (layout + table structure + client)

Both run inside one container so nothing crosses the network except our own call. The
layout model downloads at first use; weights are cached on hanna-models.

Deploy:
    MODAL_PROFILE=hanna modal deploy deploy/modal_paddle.py
Call:
    MODAL_PROFILE=hanna modal run deploy/modal_paddle.py --card <path>
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import modal

app = modal.App("hanna-paddle")

models = modal.Volume.from_name("hanna-models", create_if_missing=True)
shared_key = modal.Secret.from_name("hanna-shared-key")

# vLLM comes from PyPI; paddlepaddle-gpu needs Paddle's own index, so two pip steps.
paddle_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .apt_install("libgl1", "libglib2.0-0", "libgomp1")
    .pip_install("vllm>=0.11.0", "paddleocr[doc-parser]>=3.4", "fastapi[standard]",
                 "huggingface_hub[hf_transfer]", "safetensors")
    .pip_install("paddlepaddle-gpu==3.2.1",
                 index_url="https://www.paddlepaddle.org.cn/packages/stable/cu126/")
    .env({
        "HF_HOME": "/models/hf",
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "PADDLE_PDX_CACHE_HOME": "/models/paddlex",
        "PADDLEOCR_HOME": "/models/paddleocr",
    })
)

MODEL = "PaddlePaddle/PaddleOCR-VL-1.6"


def _wait_for_vllm(port: int, timeout: int = 1200) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(3)
    return False


@app.function(image=paddle_image, gpu="L4", volumes={"/models": models},
              max_containers=1, timeout=1800)
def parse_paddle(image_bytes: bytes) -> dict:
    """One image in, PaddleOCR-VL markdown/JSON out. Nothing is stored."""
    import tempfile

    started = time.time()
    log: list[str] = []

    def run(cmd, timeout=1500):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        log.append(f"{' '.join(cmd)[:120]} -> {result.returncode} "
                   f"{(result.stderr or result.stdout or '')[-200:].strip()}")
        return result

    server = subprocess.Popen(
        ["vllm", "serve", MODEL, "--host", "127.0.0.1", "--port", "8000",
         "--trust-remote-code", "--max-num-batched-tokens", "16384",
         "--no-enable-prefix-caching", "--mm-processor-cache-gb", "0",
         "--served-model-name", MODEL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ready = _wait_for_vllm(8000)

    markdown = ""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "card.jpg"
        source.write_bytes(image_bytes)
        out = Path(tmp) / "out"
        out.mkdir()

        script = f'''
import json
from paddleocr import PaddleOCRVL
pipeline = PaddleOCRVL(
    pipeline_version="v1.6",
    vl_rec_backend="vllm-server",
    vl_rec_server_url="http://127.0.0.1:8000/v1",
    vl_rec_api_model_name="{MODEL}",
)
for result in pipeline.predict("{source}"):
    result.save_to_markdown(save_path="{out}")
    result.save_to_json(save_path="{out}")
'''
        run([sys.executable, "-c", script], timeout=1200)
        for path in sorted(out.rglob("*.md")):
            markdown += path.read_text(errors="ignore")

    server.terminate()
    return {
        "reader": "paddleocr-vl",
        "model": MODEL,
        "vllm_ready": ready,
        "seconds": round(time.time() - started, 1),
        "ok": bool(markdown),
        "markdown": markdown[:200000],
        "setup_log": log[:8],
        "stored": False,
    }


from fastapi import FastAPI, File, Header, HTTPException, UploadFile  # noqa: E402

web = FastAPI(title="HANNA paddle")


def _authorise(key):
    expected = os.environ.get("HANNA_KEY")
    if not expected or key != expected:
        raise HTTPException(401, "chave inválida")


@web.get("/healthz")
def healthz():
    return {"ok": True, "reader": "paddleocr-vl"}


@web.post("/parse/paddle")
async def parse_route(file: UploadFile = File(...), x_hanna_key: str = Header(default=None)):
    _authorise(x_hanna_key)
    return await parse_paddle.remote.aio(await file.read())


@app.function(image=paddle_image, secrets=[shared_key])
@modal.asgi_app()
def api():
    return web


@app.local_entrypoint()
def main(card: str = ""):
    data = Path(card or
                "/home/sam/Documents/hanna_project/hanna_archive/versions/"
                "v4_hanna3_2025-2026/test_forms/form16.jpg").read_bytes()
    result = parse_paddle.remote(data)
    print({k: result[k] for k in ("reader", "vllm_ready", "seconds", "ok")})
    print("markdown chars:", len(result["markdown"]))
    for line in result["setup_log"]:
        print("log:", line[:200])
    print(result["markdown"][:800])
