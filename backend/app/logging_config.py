from __future__ import annotations

import logging  # Intercept third-party stdlib records and relay them through Loguru.
import sys
import time
from types import FrameType
from typing import TYPE_CHECKING, override

from loguru import logger

from app.request_id import request_id_var


if TYPE_CHECKING:
    import loguru


_last_log_time = time.perf_counter()

_process_start = time.perf_counter()


_PRETTY_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>{extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level> "
    "<yellow>{extra[timing]}</yellow>"
)


def configure_logging(*, json: bool = False, level: str = "INFO") -> None:
    logger.remove()
    logger.configure(patcher=_patch_record)
    if json:
        logger.add(sys.stdout, level=level, serialize=True, backtrace=False, diagnose=False)
    else:
        logger.add(sys.stdout, level=level, format=_PRETTY_FORMAT, backtrace=True, diagnose=False)

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "sqlalchemy.engine"):
        std = logging.getLogger(name)
        std.handlers = [_InterceptHandler()]
        std.propagate = False
    # Uvicorn's access formatter includes the raw path and query string. The
    # request middleware emits a structured, route-template-only completion log.
    logging.getLogger("uvicorn.access").disabled = True


def _patch_record(record: loguru.Record) -> None:
    record["extra"].setdefault("request_id", request_id_var.get())
    delta = _delta_ms()
    elapsed = _process_elapsed_ms()
    record["extra"]["delta_ms"] = round(delta, 1)
    record["extra"]["process_elapsed_ms"] = round(elapsed, 1)
    record["extra"]["timing"] = f"| +{delta:6.1f}ms ({elapsed / 1000:5.1f}s)"


def _delta_ms() -> float:
    global _last_log_time
    now = time.perf_counter()
    delta = (now - _last_log_time) * 1000
    _last_log_time = now
    return delta


def _process_elapsed_ms() -> float:
    return (time.perf_counter() - _process_start) * 1000


class _InterceptHandler(logging.Handler):
    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
