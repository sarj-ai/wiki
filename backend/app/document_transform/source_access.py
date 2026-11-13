from dataclasses import dataclass, field
import re
from typing import Any, Literal

from app.identifiers import DriveId


@dataclass(frozen=True, slots=True)
class PersonalDrive:
    kind: Literal["personal"] = "personal"


@dataclass(frozen=True, slots=True)
class SharedDrive:
    drive_id: DriveId
    kind: Literal["shared"] = "shared"


@dataclass(frozen=True, slots=True)
class UnresolvedDrive:
    kind: Literal["unresolved"] = "unresolved"


type DriveLocation = PersonalDrive | SharedDrive | UnresolvedDrive


@dataclass(frozen=True, slots=True)
class NativeSourceAccess:
    owned_by_me: bool = False
    can_edit: bool = False
    location: DriveLocation = field(default_factory=UnresolvedDrive)


def native_source_access(metadata: dict[str, Any]) -> NativeSourceAccess:
    drive = metadata.get("driveId")
    location: DriveLocation
    if "driveId" not in metadata:
        location = PersonalDrive()
    elif isinstance(drive, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", drive) is not None:
        location = SharedDrive(DriveId(drive))
    else:
        location = UnresolvedDrive()
    capabilities = metadata.get("capabilities")
    restrictions = metadata.get("contentRestrictions", [])
    editable = (
        not isinstance(location, UnresolvedDrive)
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
        location=location,
    )
