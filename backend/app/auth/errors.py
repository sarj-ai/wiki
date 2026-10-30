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
    redirect_key = "auth_failed"
