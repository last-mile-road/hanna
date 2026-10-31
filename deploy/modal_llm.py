"""One model, one server: Qwen3.5-9B on an L40S, OpenAI-compatible.

Deliberately the simplest shape Modal documents — one `vllm serve`, one web server, no
proxy, no second process. The consolidated box failed at request routing, so this exists to
give us one endpoint that definitely answers before we add anything to it.

    POST <url>/v1/chat/completions   (Bearer VLLM_API_KEY)
    GET  <url>/v1/models

scaledown_window 600 s: warm for ten minutes after the last call, then it goes away.
"""

import os
import subprocess

import modal

app = modal.App("hanna-llm")

models = modal.Volume.from_name("hanna-models", create_if_missing=True)
vllm_key = modal.Secret.from_name("hanna-vllm-key")

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm>=0.11.0", "huggingface_hub[hf_transfer]")
    .env({
        "HF_HOME": "/models/hf",
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        # vLLM's default sampling path JIT-builds a FlashInfer kernel with nvcc inside the
        # container, and that build fails. Without this the engine dies at startup.
        "VLLM_USE_FLASHINFER_SAMPLER": "0",
    })
)

MODEL = "Qwen/Qwen3.5-9B"


@app.function(
    image=image,
    gpu="L40S",
    volumes={"/models": models},
    secrets=[vllm_key],
    max_containers=1,
    timeout=24 * 3600,
    scaledown_window=600,
)
@modal.web_server(port=8000, startup_timeout=3600)
def serve():
    subprocess.Popen([
        "vllm", "serve", MODEL,
        "--host", "0.0.0.0",
        "--port", "8000",
        "--api-key", os.environ.get("VLLM_API_KEY", ""),
        "--served-model-name", "hanna-9b",
        "--max-model-len", "32768",
        "--gpu-memory-utilization", "0.90",
        "--limit-mm-per-prompt", '{"image": 2}',
        "--mm-processor-kwargs", '{"max_pixels": 4000000}',
    ])
