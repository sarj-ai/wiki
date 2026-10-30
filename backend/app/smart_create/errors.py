from __future__ import annotations

from app.files.errors import FilesError


class ReorganizationUnavailableError(FilesError):
    http_status = 503
    code = "reorganization_unavailable"

    def __init__(self) -> None:
        super().__init__("Re-organize could not evaluate this document; try again shortly")


class TemplateChangedError(FilesError):
    http_status = 409
    code = "template_changed"

    def __init__(self) -> None:
        super().__init__("The Drive template changed; refresh templates and review again")


class TemplateUnavailableError(FilesError):
    http_status = 409
    code = "template_unavailable"

    def __init__(self) -> None:
        super().__init__("The selected template is no longer available in this workspace")
