"""Auth DTOs shared beyond the auth router.

``UserProfile`` started life inside ``auth/router.py``; it lives here
so the bootstrap aggregate can return it without importing a router
(the import-linter contracts forbid upward service→router imports).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.models.user import User


class UserProfile(BaseModel):
    id: str
    email: str
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
