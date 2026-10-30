from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.aliases.schemas import DocumentAliasValue
from app.extras.schemas import IconStr
from app.files.schemas import FileName, FileResponse
from app.identifiers import DriveId, FileId, TaskId
from app.templates.schemas import TemplateMimeType


SmartDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)
]
SmartFileType = Literal["document", "presentation"]


class StrictSmartModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AIMode(StrictSmartModel):
    kind: Literal["ai"]
    description: SmartDescription
    file_type: SmartFileType
    invoking_folder_id: FileId | None = None
    invoking_drive_id: DriveId | None = None


class BlankSource(StrictSmartModel):
    kind: Literal["blank"]
    file_type: SmartFileType


class TemplateSource(StrictSmartModel):
    kind: Literal["template"]
    template_file_id: FileId
    template_drive_id: DriveId
    template_modified_time: str
    mime_type: TemplateMimeType


CreationSource = Annotated[BlankSource | TemplateSource, Field(discriminator="kind")]


class ProposalDestination(StrictSmartModel):
    folder_id: FileId
    drive_id: DriveId
    drive_name: str
    folder_path: list[str]


class SmartProposalRead(StrictSmartModel):
    name: FileName
    emoji: IconStr
    alias: DocumentAliasValue
    destination: ProposalDestination
    source: CreationSource


class ReorganizationProposalRead(StrictSmartModel):
    current_destination: ProposalDestination
    suggested_destination: ProposalDestination | None
    reason: str | None


class SmartFileCreate(StrictSmartModel):
    source: CreationSource
    parent_folder_id: FileId
    name: FileName
    emoji: IconStr
    alias: DocumentAliasValue


class SmartCreateWarning(StrictSmartModel):
    code: str
    detail: str


class SmartCreateResult(StrictSmartModel):
    task_id: TaskId
    file: FileResponse
    warnings: list[SmartCreateWarning]
