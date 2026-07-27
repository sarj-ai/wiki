from datetime import UTC, datetime, timedelta
from typing import Literal
import uuid

import jwt
from pydantic import BaseModel, NonNegativeInt, ValidationError

from app.auth.errors import InvalidTokenError
from app.config import settings


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class TokenPayload(BaseModel):
    sub: uuid.UUID
    type: Literal["access", "refresh"]
    tv: NonNegativeInt = 0


def create_access_token(user_id: uuid.UUID, email: str, token_version: int = 0) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
        "tv": token_version,
    }
    return jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)


def create_refresh_token(user_id: uuid.UUID, token_version: int = 0) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
        "tv": token_version,
    }
    return jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)


def decode_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY.get_secret_value(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as e:
        msg = "Invalid or expired token"
        raise InvalidTokenError(msg) from e
    try:
        return TokenPayload.model_validate(payload)
    except ValidationError as e:
        msg = "Invalid token: malformed claims"
        raise InvalidTokenError(msg) from e
