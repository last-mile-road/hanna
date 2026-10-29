"""OpenRouter inference. One path, one key, no local GPU."""

from __future__ import annotations

import base64
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        self.client = OpenAI(
            api_key=key,
            base_url=base_url or settings.OPENROUTER_BASE_URL,
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
        extra_body: dict[str, Any] = {"usage": {"include": True}}
        if model_settings.get("reasoning_effort"):
            extra_body["reasoning"] = {"effort": model_settings["reasoning_effort"]}
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": float(model_settings.get("temperature", 0)),
            "max_tokens": int(model_settings.get("max_tokens", 1500)),
            "extra_body": extra_body,
        }
        if model_settings.get("top_p") is not None:
            kwargs["top_p"] = float(model_settings["top_p"])
        response = self.client.chat.completions.create(**kwargs)
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
