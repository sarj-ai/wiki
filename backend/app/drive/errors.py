from __future__ import annotations


class DrivePermissionsError(Exception):
    http_status: int = 502
    code: str = "drive_permissions_upstream"


class DrivePermissionsForbiddenError(DrivePermissionsError):
    http_status = 403
    code = "drive_permissions_forbidden"

    def __init__(self) -> None:
        super().__init__("Google Drive did not allow this account to view drive access")


class DrivePermissionsNotFoundError(DrivePermissionsError):
    http_status = 404
    code = "drive_not_found"

    def __init__(self) -> None:
        super().__init__("Drive not found")


class DrivePermissionsUpstreamError(DrivePermissionsError):
    def __init__(self) -> None:
        super().__init__("Google Drive could not return drive access")


class DriveTraversalIncompleteError(Exception):
    pass
