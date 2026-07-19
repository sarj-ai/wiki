from __future__ import annotations

from pydantic import BaseModel


class LinkMeta(BaseModel):
    name: str | None
