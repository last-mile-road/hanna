"""How the review screen lays a form out.

Per form variant first (ui.<variant>.yaml — the back card has no map and different
sections), then the country default (ui.yaml). Kept here, not in the API module,
because the run read model needs the same dev-only list to know what a reviewer
can actually decide.
"""

from __future__ import annotations

from typing import Any

import yaml

from . import settings

_CACHE: dict[str, dict[str, Any]] = {}


def layout(form_definition: dict[str, Any] | None) -> dict[str, Any]:
    definition = form_definition or {}
    country = definition.get("country", "mozambique")
    variant = definition.get("variant") or "draft"
    key = f"{country}:{variant}"
    if key not in _CACHE:
        directory = settings.COUNTRIES_DIR / country
        variant_path = directory / f"ui.{variant}.yaml"
        default_path = directory / "ui.yaml"
        path = variant_path if variant_path.exists() else default_path
        _CACHE[key] = yaml.safe_load(path.read_text()) if path.exists() else {}
    return _CACHE[key]
