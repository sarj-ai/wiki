from dataclasses import dataclass
import re
from typing import Any

from app.identifiers import DriveId


@dataclass(frozen=True, slots=True)
class NativeSourceAccess:
    owned_by_me: bool
    can_edit: bool
    is_personal_drive: bool
    shared_drive_id: DriveId | None


def native_source_access(metadata: dict[str, Any]) -> NativeSourceAccess:
    drive = metadata.get("driveId")
    personal = "driveId" not in metadata
    shared_id = (
        DriveId(drive)
        if isinstance(drive, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", drive) is not None
        else None
    )
    capabilities = metadata.get("capabilities")
    restrictions = metadata.get("contentRestrictions", [])
    editable = (
        (personal or shared_id is not None)
        and metadata.get("trashed", False) is False
        and isinstance(capabilities, dict)
        and capabilities.get("canEdit") is True
        and isinstance(restrictions, list)
        and all(
            isinstance(restriction, dict) and restriction.get("readOnly") is False
            for restriction in restrictions
        )
    )
    return NativeSourceAccess(
        owned_by_me=metadata.get("ownedByMe") is True,
        can_edit=editable,
        is_personal_drive=personal,
        shared_drive_id=shared_id,
    )
