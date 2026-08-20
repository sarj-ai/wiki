from __future__ import annotations


class FilesError(Exception):
    http_status: int = 500
    code: str = "files_error"

    @property
    def public_detail(self) -> str:
        return str(self)


class UnsupportedMimeError(FilesError):
    http_status = 400
    code = "unsupported_mime"


class FileNotInScopeError(FilesError):
    http_status = 404
    code = "file_not_in_scope"


class FolderCopyNotSupportedError(FilesError):
    http_status = 400
    code = "folder_copy_not_supported"


class CrossDriveMoveNotSupportedError(FilesError):
    http_status = 400
    code = "cross_drive_move_not_supported"


class DriveUpstreamError(FilesError):
    code = "drive_upstream"
    message = "Google Drive request could not be completed"

    def __init__(self, status: int, *, reason: str | None = None) -> None:
        super().__init__(self.message)
        self.http_status = status if 400 <= status < 500 else 502
        self.reason = reason


class FileVersionChangedError(FilesError):
    http_status = 409
    code = "file_version_changed"

    def __init__(self) -> None:
        super().__init__("The source file changed; refresh and review again")
