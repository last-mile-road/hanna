"""OpenRouter inference. One path, one key, no local GPU."""

from __future__ import annotations

import base64
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openai
from openai import OpenAI

from . import settings


@dataclass
class Completion:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int
    usage: dict[str, Any]


class OpenRouter:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        key = api_key or settings.OPENROUTER_API_KEY
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self.base_url = base_url or settings.OPENROUTER_BASE_URL
        # A self-hosted OpenAI-compatible server (the Modal box) rejects or ignores
        # OpenRouter's extension fields, and needs its own key name.
        self.self_hosted = "openrouter" not in self.base_url
        self.client = OpenAI(
            api_key=key,
            base_url=self.base_url,
            timeout=120.0,
            max_retries=1,
        )

    @staticmethod
    def _image_part(path: Path) -> dict[str, Any]:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}

    def complete(
        self,
        prompt: str,
        image_paths: list[Path],
        model: str,
        model_settings: dict[str, Any] | None = None,
    ) -> Completion:
        model_settings = model_settings or {}
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content += [self._image_part(path) for path in image_paths]

        started = time.monotonic()
        if self.self_hosted:
            # vLLM knows nothing about `usage.include` or OpenRouter's reasoning effort;
            # it rejects unknown fields, so send only the standard body.
            extra_body: dict[str, Any] = {}
        else:
            extra_body = {"usage": {"include": True}}
            # OpenRouter's reasoning control: either a full map
            # ({"enabled": false} or {"effort": "low"}) or the short effort key.
            if model_settings.get("reasoning") is not None:
                extra_body["reasoning"] = model_settings["reasoning"]
            elif model_settings.get("reasoning_effort"):
                extra_body["reasoning"] = {"effort": model_settings["reasoning_effort"]}
            # Some Qwen templates (Qwen3.5 included) only honour the switch in the
            # chat template. OpenRouter passes this through to the upstream server.
            if "enable_thinking" in model_settings:
                extra_body["chat_template_kwargs"] = {
                    "enable_thinking": bool(model_settings["enable_thinking"])
                }
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": float(model_settings.get("temperature", 0)),
            "max_tokens": int(model_settings.get("max_tokens", 1500)),
            "extra_body": extra_body,
        }
        if model_settings.get("top_p") is not None:
            kwargs["top_p"] = float(model_settings["top_p"])
        # A shared upstream pool rate-limits one model while another is healthy. One retry
        # inside the SDK is not enough; back off here instead of failing the block.
        response = None
        for attempt in range(4):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except (openai.RateLimitError, openai.APITimeoutError,
                    openai.APIConnectionError, openai.InternalServerError):
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        latency_ms = int((time.monotonic() - started) * 1000)
        text = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}
        cost = usage.get("cost")
        return Completion(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=float(cost) if cost is not None else None,
            latency_ms=latency_ms,
            usage=usage,
        )
