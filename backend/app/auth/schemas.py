from __future__ import annotations

from datetime import timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.identifiers import GoogleUserId
from app.models.user import User


class UserProfile(BaseModel):
    id: str
    email: EmailStr
    display_name: str | None
    avatar_url: str | None

    @classmethod
    def from_user(cls, user: User) -> Self:
        return cls(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
        )


class GoogleTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str
    refresh_token: str | None = None
    expires_in: timedelta = Field(gt=timedelta(0), le=timedelta(hours=24))


class GoogleUserInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: GoogleUserId
    email: EmailStr
    verified_email: Literal[True]
    name: str | None = None
    picture: str | None = None

    @field_validator("verified_email", mode="before")
    @classmethod
    def require_literal_true(cls, value: object) -> Literal[True]:
        if value is not True:
            msg = "Google email is not verified"
            raise ValueError(msg)
        return True
