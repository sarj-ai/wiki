from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


def authentication_responses() -> dict[int | str, dict[str, Any]]:
    return {
        401: {
            "model": ErrorResponse,
            "description": "Authentication is required or the session is invalid.",
        }
    }
