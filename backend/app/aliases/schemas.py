from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.identifiers import FileId


DocumentAliasValue = Annotated[
    str,
    StringConstraints(
        min_length=5,
        max_length=32,
        pattern=r"^[a-z]{2,12}(?:-[a-z]{2,12}){1,2}$",
    ),
]


class DocumentAliasWrite(BaseModel):
    alias: DocumentAliasValue


class DocumentAliasRead(BaseModel):
    alias: DocumentAliasValue
    file_id: FileId


class DocumentAliasForFile(BaseModel):
    alias: DocumentAliasValue | None
