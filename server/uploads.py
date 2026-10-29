"""Upload validation and storage. No image enters the repo tree, and no file keeps the
name the browser sent."""

from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import numpy as np

from core import settings

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}


def validate(filename: str | None, content_type: str | None, size: int) -> str | None:
    """Return an error message, or None when the upload is acceptable."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return "Formato não aceite. Use JPG ou PNG."
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        return "Tipo de ficheiro não aceite. Use JPG ou PNG."
    if size <= 0:
        return "Ficheiro vazio."
    if size > settings.MAX_UPLOAD_BYTES:
        return (
            f"Ficheiro demasiado grande ({size // 1024} KB). "
            f"Limite: {settings.MAX_UPLOAD_BYTES // 1024} KB."
        )
    return None


def decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("A imagem não pôde ser lida. Fotografe novamente.")
    return image


def check_quality(image: np.ndarray) -> str | None:
    """Cheap server-side floor. The richer gate (blur, glare, alignment) is Phase 2."""
    height, width = image.shape[:2]
    scale = 600 / max(height, width)
    thumb = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))))
    gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
    if cv2.Laplacian(gray, cv2.CV_64F).var() < 5:
        return (
            "A fotografia parece estar em branco ou sem contraste. "
            "Verifique se o cartão está no enquadramento e a câmara a focar."
        )
    long_edge = max(height, width)
    if long_edge < settings.MIN_UPLOAD_LONG_EDGE:
        return (
            f"Resolução demasiado baixa ({long_edge} px no lado maior). "
            f"Mínimo: {settings.MIN_UPLOAD_LONG_EDGE} px."
        )
    return None


def save(data: bytes, suffix: str) -> Path:
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = settings.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)
    return path
