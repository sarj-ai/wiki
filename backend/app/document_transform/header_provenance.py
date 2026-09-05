from __future__ import annotations

from typing import Annotated, Literal, Self
import uuid

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.document_transform.ir import MAX_DOC_TABS
from app.identifiers import DocsSegmentId, DocsTabId, FileId


HeaderDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ManagedHeaderIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tab_id: DocsTabId
    header_id: DocsSegmentId
    fingerprint_sha256: HeaderDigest


class HeaderIdentityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1, 2] = 1
    operation_id: uuid.UUID
    file_id: FileId
    profile_sha256: HeaderDigest
    title_sha256: HeaderDigest
    tab_ids: tuple[DocsTabId, ...] = Field(min_length=1, max_length=MAX_DOC_TABS)
    headers: tuple[ManagedHeaderIdentity, ...] = Field(min_length=1, max_length=MAX_DOC_TABS)

    @model_validator(mode="after")
    def unique_bindings(self) -> Self:
        if len({header.tab_id for header in self.headers}) != len(self.headers):
            msg = "Managed header tabs must be unique"
            raise ValueError(msg)
        if len(set(self.tab_ids)) != len(self.tab_ids) or not {
            header.tab_id for header in self.headers
        }.issubset(self.tab_ids):
            msg = "Managed headers must belong to the recorded topology"
            raise ValueError(msg)
        return self


class HeaderIdentityFinalization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    result_sha256: HeaderDigest
    verified_revision_sha256: HeaderDigest


class HeaderIdentityProof(HeaderIdentityFinalization):
    evidence: HeaderIdentityEvidence
