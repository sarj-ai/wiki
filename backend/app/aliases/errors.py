from __future__ import annotations


class AliasError(Exception):
    http_status: int = 500
    code: str = "alias_error"
    message: str = "Short link operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class AliasNotFoundError(AliasError):
    http_status = 404
    code = "alias_not_found"
    message = "Short link not found"


class AliasConflictError(AliasError):
    http_status = 409
    code = "alias_conflict"
    message = "That short link is already reserved"


class AliasEditForbiddenError(AliasError):
    http_status = 403
    code = "alias_edit_forbidden"
    message = "Editor access is required to manage this short link"


class AliasTargetUnsupportedError(AliasError):
    http_status = 400
    code = "alias_target_unsupported"
    message = "Short links are only available for documents"
