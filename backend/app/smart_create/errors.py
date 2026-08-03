from __future__ import annotations

from app.files.errors import FilesError


class SmartCreateTaskFailedError(FilesError):
    http_status = 409
    code = "smart_create_task_failed"

    def __init__(self, *, detail: str | None = None) -> None:
        super().__init__(detail or "Smart Create task failed")


class SmartCreateTaskNotAdmittedError(FilesError):
    http_status = 409
    code = "smart_create_task_not_admitted"

    def __init__(self) -> None:
        super().__init__(
            "The original request was not admitted; retry with the same idempotency key"
        )


class SmartCreateTaskInProgressError(FilesError):
    http_status = 409
    code = "smart_create_task_in_progress"

    def __init__(self) -> None:
        super().__init__("The matching Smart Create request is still running; retry shortly")


class SmartCreateTaskCancelledError(FilesError):
    http_status = 409
    code = "smart_create_task_cancelled"

    def __init__(self) -> None:
        super().__init__("Smart Create task was cancelled")


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
