from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from typing_extensions import Self


# ------------------------------------------------------------------
# Request schemas
# ------------------------------------------------------------------

class PermissionCreate(BaseModel):
    page_id: UUID
    user_id: UUID | None = None
    workspace_id: UUID | None = None
    permission: Literal["view", "edit"]

    @model_validator(mode="after")
    def at_least_one_grantee(self) -> Self:
        if self.user_id is None and self.workspace_id is None:
            raise ValueError("Either user_id or workspace_id must be provided")
        return self


# ------------------------------------------------------------------
# Response schemas
# ------------------------------------------------------------------

class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    page_id: UUID
    user_id: UUID | None
    workspace_id: UUID | None
    permission: str
    display_name: str | None = None
    email: str | None = None


class UserSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
