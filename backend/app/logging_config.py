from __future__ import annotations

import logging
import sys
from types import FrameType
from typing import TYPE_CHECKING, override

from loguru import logger

from app.request_id import request_id_var


if TYPE_CHECKING:
    import loguru


_PRETTY_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>{extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


def configure_logging(*, json: bool = False, level: str = "INFO") -> None:
    logger.remove()
    logger.configure(patcher=_bind_request_id)
    if json:
        logger.add(sys.stdout, level=level, serialize=True, backtrace=False, diagnose=False)
    else:
        logger.add(sys.stdout, level=level, format=_PRETTY_FORMAT, backtrace=True, diagnose=False)

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "sqlalchemy.engine"):
        std = logging.getLogger(name)
        std.handlers = [_InterceptHandler()]
        std.propagate = False


def _bind_request_id(record: loguru.Record) -> None:
    record["extra"].setdefault("request_id", request_id_var.get())


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


__all__: list[str] = ["configure_logging", "logger"]
