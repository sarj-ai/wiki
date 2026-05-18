"""Typed errors raised by the auth service and JWT layer.

Each subclass carries a ``redirect_key`` the OAuth callback router maps
to the frontend's ``?error=...`` param. Other entry points (refresh,
``get_current_user``) translate ``AuthError`` to HTTP 401 at the app
boundary; the service layer never imports ``fastapi``.
"""

from __future__ import annotations


class AuthError(Exception):
    redirect_key: str = "server_error"


class AuthFailedError(AuthError):
    redirect_key = "auth_failed"


class DomainRestrictedError(AuthError):
    redirect_key = "domain_restricted"


class TokenRevokedError(AuthError):
    redirect_key = "auth_failed"


class InvalidTokenError(AuthError):
    """Decoding failed (bad signature, missing claims, expiry)."""

    redirect_key = "auth_failed"
