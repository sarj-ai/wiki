from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.user import User


class UserProfile(BaseModel):
    id: str
    email: EmailStr
    display_name: str | None
    avatar_url: str | None

    @classmethod
    def from_user(cls, user: User) -> UserProfile:
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
    expires_in: timedelta | None = None


class GoogleUserInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    email: EmailStr
    name: str | None = None
    picture: str | None = None
