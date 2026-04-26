"""Typed errors raised by ``FilesService``.

The service layer never imports ``fastapi``; HTTP-status mapping happens
at the app boundary via the exception handler installed in ``main.py``.
Each subclass carries the status it should surface and a stable string
``code`` clients can match on.
"""

from __future__ import annotations


class FilesError(Exception):
    http_status: int = 500
    code: str = "files_error"


class UnsupportedMimeError(FilesError):
    http_status = 400
    code = "unsupported_mime"


class FileNotInScopeError(FilesError):
    http_status = 404
    code = "file_not_in_scope"


class FolderCopyNotSupportedError(FilesError):
    http_status = 400
    code = "folder_copy_not_supported"


class DriveUpstreamError(FilesError):
    code = "drive_upstream"

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        # Drive 4xx surfaces directly; 5xx and transport bubble as 502.
        self.http_status = status if 400 <= status < 500 else 502
