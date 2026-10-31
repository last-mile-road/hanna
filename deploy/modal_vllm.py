"""Our own Qwen VL models on Modal, served by vLLM.

Why: OpenRouter rate-limits the shared upstream pool (the 8B was throttled for most of
2026-10-31), and a self-hosted endpoint removes the third-party model provider from the
path.

Sizing, from the model cards: Qwen3-VL-8B-Instruct and Qwen3.5-9B are both ~9B parameters,
about 21–23 GB at fp16, so an **L4 (24 GB)** holds one of them unquantised. A T4 (16 GB)
would need 4-bit, which is exactly where the OCR comparison showed quantisation costing
accuracy, so: fp16 on L4, one model per container.

Each model is its own web server on port 8000, OpenAI-compatible. `max_model_len` is 8192
because our prompts are one image and a few hundred tokens; the default 262k would reserve
far more KV cache than we use.

Deploy:
    MODAL_PROFILE=hanna modal deploy deploy/modal_vllm.py
"""

import subprocess

import modal

app = modal.App("hanna-vllm")

models = modal.Volume.from_name("hanna-models", create_if_missing=True)

vllm_key = modal.Secret.from_name("hanna-vllm-key")

vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm>=0.11.0", "huggingface_hub[hf_transfer]", "fastapi[standard]")
    .env({"HF_HOME": "/models/hf", "HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

MODELS = {
    "8b": "Qwen/Qwen3-VL-8B-Instruct",
    "9b": "Qwen/Qwen3.5-9B",
}


import os


def _serve(repo: str) -> None:
    api_key = os.environ.get("VLLM_API_KEY", "")
    subprocess.Popen(
        [
            "vllm", "serve", repo,
            "--api-key", api_key,
            "--host", "0.0.0.0",
            "--port", "8000",
            "--max-model-len", "8192",
            "--gpu-memory-utilization", "0.90",
            "--limit-mm-per-prompt", '{"image": 2}',
            "--disable-log-requests",
        ]
    )


@app.function(
    image=vllm_image,
    gpu="L4",
    volumes={"/models": models},
    secrets=[vllm_key],
    max_containers=1,
    timeout=24 * 3600,
    scaledown_window=600,
)
@modal.web_server(port=8000, startup_timeout=1800)
def serve_8b():
    _serve(MODELS["8b"])


@app.function(
    image=vllm_image,
    gpu="L4",
    volumes={"/models": models},
    secrets=[vllm_key],
    max_containers=1,
    timeout=24 * 3600,
    scaledown_window=600,
)
@modal.web_server(port=8000, startup_timeout=1800)
def serve_9b():
    _serve(MODELS["9b"])
