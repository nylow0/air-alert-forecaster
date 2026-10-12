"""News/settings/auth feature router; include it from the lead's FastAPI app."""

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from airalert.news.codex_runner import CodexError
from airalert.news.worker import get_worker
from airalert.schemas import AuthStatus, NewsFeed, Settings

router = APIRouter()


def loopback_mutation(request: Request) -> None:
    """Reject non-loopback clients, DNS-rebinding hosts and cross-origin browser requests."""
    try:
        if request.client is None:
            raise ValueError("Missing client")
        ip = ipaddress.ip_address(request.client.host)
        mapped = getattr(ip, "ipv4_mapped", None)
        if not (mapped or ip).is_loopback:
            raise ValueError("Non-loopback client")
        host = request.headers.get("host", "")
        parsed = urlsplit(f"http://{host}")
        hostname = parsed.hostname
        if hostname != "localhost" and not ipaddress.ip_address(hostname or "").is_loopback:
            raise ValueError("Non-loopback Host")
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Invalid Host")
        origin = request.headers.get("origin")
        if origin is not None:
            source = urlsplit(origin)
            target = urlsplit(f"{request.url.scheme}://{host}")
            if (
                source.scheme,
                source.hostname,
                source.port or (443 if source.scheme == "https" else 80),
            ) != (
                target.scheme,
                target.hostname,
                target.port or (443 if target.scheme == "https" else 80),
            ):
                raise ValueError("Cross-origin request")
            if (
                source.path not in {"", "/"}
                or source.query
                or source.fragment
                or source.username is not None
                or source.password is not None
            ):
                raise ValueError("Invalid Origin")
        if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            raise ValueError("Cross-origin request")
    except ValueError as exc:
        raise HTTPException(403, "News controls require a loopback, same-origin client") from exc


@router.get("/api/news", response_model=NewsFeed)
def news() -> NewsFeed:
    return get_worker().feed()


@router.get("/api/settings", response_model=Settings)
def settings() -> Settings:
    return get_worker().settings.load()


@router.put("/api/settings", response_model=Settings, dependencies=[Depends(loopback_mutation)])
def save_settings(value: Settings) -> Settings:
    worker = get_worker()
    try:
        previous = worker.settings.load()
        if not value.news_enabled and value.models == previous.models:
            # Turning news off must remain possible even when Codex is missing or unavailable.
            return worker.settings.save(value)
        auth = worker.auth.status(refresh=True)
        if not auth.codex_installed:
            raise HTTPException(409, "Codex model discovery is required before changing settings")
        return worker.settings.save(value, worker.auth.models)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Invalid news settings or unavailable model/effort") from exc


@router.get("/api/auth/status", response_model=AuthStatus)
def auth_status() -> AuthStatus:
    return get_worker().auth.status()


@router.post(
    "/api/auth/login", response_model=AuthStatus, dependencies=[Depends(loopback_mutation)]
)
def login() -> AuthStatus:
    try:
        return get_worker().auth.login()
    except (CodexError, OSError) as exc:
        raise HTTPException(409, "Codex login could not be started") from exc


@router.post(
    "/api/auth/cancel", response_model=AuthStatus, dependencies=[Depends(loopback_mutation)]
)
def cancel() -> AuthStatus:
    return get_worker().auth.cancel()


@router.post(
    "/api/auth/logout", response_model=AuthStatus, dependencies=[Depends(loopback_mutation)]
)
def logout() -> AuthStatus:
    """Explicit Codex logout affects every Codex use on this machine, including other sessions."""
    worker = get_worker()
    worker.changed()
    worker.runner.cancel()
    try:
        return worker.auth.logout()
    except (CodexError, OSError) as exc:
        raise HTTPException(
            409, "Codex logout failed. Logout affects every Codex use on this machine."
        ) from exc
