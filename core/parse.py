"""Robust JSON extraction from a VLM response.

Carried over from v5 `utils.extract_json_from_vlm_response`, which exists because
model output is not reliably valid JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _normalise_keys(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            clean = str(key).strip().lower().replace(" ", "_").replace("-", "_")
            clean = re.sub(r"_+", "_", clean)
            out[clean] = _normalise_keys(value)
        return out
    if isinstance(data, list):
        return [_normalise_keys(item) for item in data]
    return data


def _strip_code_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _repair(text: str) -> str:
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def parse_json_response(
    response_text: str | None, expected_fields: list[str] | None = None
) -> dict[str, Any]:
    if not response_text or not isinstance(response_text, str):
        return {}

    text = _strip_code_fence(response_text.strip())

    for candidate in (text, _repair(text)):
        try:
            return _normalise_keys(json.loads(candidate))
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidate = _repair(text[start : end + 1])
        try:
            return _normalise_keys(json.loads(candidate))
        except json.JSONDecodeError:
            pass

    if expected_fields:
        found: dict[str, Any] = {}
        for field in expected_fields:
            pattern = rf'"{re.escape(field)}"\s*:\s*(?:"([^"]*)"|([^,\s}}\]]+))'
            match = re.search(pattern, text)
            if match:
                value = match.group(1) if match.group(1) is not None else match.group(2)
                if value is not None:
                    found[field] = value.strip().strip('"').strip("'")
        return _normalise_keys(found)

    return {}
