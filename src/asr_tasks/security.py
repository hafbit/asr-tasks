from __future__ import annotations

import hmac
import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from fastapi.security.utils import get_authorization_scheme_param

from .config import Settings


def auth_dependency(settings: Settings) -> Callable[[Request], None]:
    def authenticate(request: Request) -> None:
        if settings.allow_unauthenticated:
            return
        if not settings.api_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API authentication is not configured",
            )
        scheme, credentials = get_authorization_scheme_param(request.headers.get("Authorization"))
        if scheme.lower() != "bearer" or not hmac.compare_digest(credentials, settings.api_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return authenticate


def _is_blocked_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def validate_source_url(url: str, settings: Settings) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("source_url must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("source_url must not contain credentials")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        raise ValueError("source_url must contain a hostname")
    if hostname in settings.source_url_allowed_hosts:
        return url

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as error:
        raise ValueError(f"source_url hostname cannot be resolved: {hostname}") from error
    if not addresses:
        raise ValueError(f"source_url hostname cannot be resolved: {hostname}")
    if any(_is_blocked_ip(address) for address in addresses):
        raise ValueError("source_url resolves to a blocked network address")
    return url
