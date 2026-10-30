from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum, auto


class NativeOAuthRequestError(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid OAuth request")


@dataclass(frozen=True, slots=True)
class NativeOAuthRequestContext:
    resource: str
    browser_binding: str | None
    endpoint: NativeOAuthEndpoint


class NativeOAuthEndpoint(StrEnum):
    AUTHORIZE = "authorize"
    TOKEN = auto()
    REGISTER = "register"
    REVOKE = "revoke"


native_oauth_request: ContextVar[NativeOAuthRequestContext | None] = ContextVar(
    "native_oauth_request", default=None
)


def require_native_oauth_request(expected_resource: str) -> NativeOAuthRequestContext:
    context = native_oauth_request.get()
    if context is None or context.resource != expected_resource:
        raise NativeOAuthRequestError
    return context
