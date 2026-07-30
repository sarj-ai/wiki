from collections.abc import Callable
from typing import cast

from slowapi import Limiter
from slowapi.util import get_remote_address


limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


def rate_limit[F: Callable[..., object]](spec: str) -> Callable[[F], F]:
    """Typed facade over `Limiter.limit`, whose decorator slowapi leaves untyped.

    The decorator wraps the endpoint without changing its signature, which the
    cast states once here instead of per-route suppressions.

    Returns:
        A decorator that registers `spec` for the wrapped endpoint.

    """
    return cast("Callable[[F], F]", limiter.limit(spec))


def rate_limit_exempt[F: Callable[..., object]](endpoint: F) -> F:
    """Typed facade over `Limiter.exempt`; same contract as `rate_limit`.

    Returns:
        The endpoint, registered as exempt from the default limits.

    """
    return cast("F", limiter.exempt(endpoint))
