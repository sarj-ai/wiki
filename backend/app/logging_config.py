from __future__ import annotations

import logging
import sys
from types import FrameType
from typing import override

from loguru import logger


def configure_logging(*, json: bool = False, level: str = "INFO") -> None:
    logger.remove()
    if json:
        logger.add(sys.stdout, level=level, serialize=True, backtrace=False, diagnose=False)
    else:
        logger.add(sys.stdout, level=level, backtrace=True, diagnose=False)

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi", "sqlalchemy.engine"):
        std = logging.getLogger(name)
        std.handlers = [_InterceptHandler()]
        std.propagate = False


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
