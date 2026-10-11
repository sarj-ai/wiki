import time
from typing import Literal
import uuid

import jwt
from pydantic import BaseModel, NonNegativeInt, ValidationError

from app.auth.errors import InvalidTokenError
from app.config import settings
from app.identifiers import AuthSessionId, TokenId, UserId


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7
JWT_ISSUER = "sarj-wiki"
JWT_AUDIENCE = "sarj-wiki-api"
# Compatibility exports for callers that imported the original public names.
TOKEN_ISSUER = JWT_ISSUER
TOKEN_AUDIENCE = JWT_AUDIENCE


class TokenPayload(BaseModel):
    sub: UserId
    sid: AuthSessionId
    jti: TokenId
    type: Literal["access", "refresh"]
    tv: NonNegativeInt = 0


def create_access_token(
    user_id: UserId,
    session_id: AuthSessionId,
    token_version: int = 0,
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(uuid.uuid7()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "type": "access",
        "tv": token_version,
    }
    return jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)


def create_refresh_token(
    user_id: UserId,
    session_id: AuthSessionId,
    refresh_jti: TokenId,
    token_version: int = 0,
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(refresh_jti),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        "type": "refresh",
        "tv": token_version,
    }
    return jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)


def decode_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["aud", "exp", "iat", "iss", "jti", "sid", "sub", "type"]},
        )
    except jwt.PyJWTError as e:
        msg = "Invalid or expired token"
        raise InvalidTokenError(msg) from e
    try:
        return TokenPayload.model_validate(payload)
    except ValidationError as e:
        msg = "Invalid token: malformed claims"
        raise InvalidTokenError(msg) from e
