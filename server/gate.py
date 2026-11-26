"""One gate over the API, switched on by HANNA_AUTH_REQUIRED.

Identity is an account cookie or a field cookie. Public by design: the country
list, the QR, the sign-in endpoints and the health check.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core import settings
from server.identity import current_identity

PUBLIC_PREFIXES = (
    "/auth/",
    "/field/login",
    "/field/register",
    "/api/countries",
    "/static/",
    "/c/",
    "/invite/",
    "/login",
    "/healthz",
    "/favicon",
)


class AuthGate(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not settings.AUTH_REQUIRED:
            return await call_next(request)

        path = request.url.path
        if path == "/" or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        identity = current_identity(request)
        if identity is not None:
            return await call_next(request)

        if path == "/capture" or path.startswith("/review") or path == "/portal":
            return RedirectResponse(f"/login?next={path}", status_code=307)
        if path.startswith("/api/") or path.startswith("/portal/api/"):
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        return await call_next(request)
