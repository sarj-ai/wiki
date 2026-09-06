from datetime import datetime
from typing import Literal

from pydantic import Field

from app.identifiers import GoogleUserId, UserId
from app.mcp.identity import McpClientId
from app.mcp.schemas import McpCapability, McpModel
from app.models.mcp_grant import McpGrantId


class NativeOAuthError(ValueError):
    def __init__(self) -> None:
        super().__init__("Wiki authorization unavailable")


class WikiConsentIdentity(McpModel):
    # Construct only after validating the current Wiki browser session.
    user_id: UserId
    session_revision: int = Field(ge=0, strict=True)


class ConsentDecision(McpModel):
    approve: bool = Field(strict=True)
    transform_approved: bool = Field(default=False, strict=True)


class NativeAuthorization(McpModel):
    allowed: bool


class NativeIntent(McpModel):
    expires_at: datetime
    issuer: str
    client_id: McpClientId
    redirect_uri: str
    redirect_explicit: bool
    resource: str
    state: str | None
    challenge: str
    scopes: list[McpCapability]
    browser_binding: str
    user_id: UserId | None = None
    session_revision: int | None = None
    csrf: str | None = None
    decided: bool = False
    decision: Literal["approved", "denied"] | None = None


class NativeCredential(McpModel):
    issuer: str
    grant_id: McpGrantId
    client_id: McpClientId
    user_id: UserId
    google_subject: GoogleUserId
    issued_at: int
    expires_at: datetime
    resource: str
    scopes: list[McpCapability]
    consumed: bool = False
    challenge: str | None = None
    redirect_uri: str | None = None
    redirect_explicit: bool = True


class ConsentView(McpModel):
    client_id: McpClientId
    client_name: str
    scopes: list[McpCapability]
    csrf: str
    status: Literal["pending", "approved", "denied"] = "pending"
    content_trust: Literal["untrusted_client_name"] = "untrusted_client_name"


class ConnectionView(McpModel):
    grant_id: McpGrantId
    client_id: McpClientId
    capabilities: list[McpCapability]
    expires_at: datetime


class ConsentStatus(McpModel):
    status: Literal["pending", "approved", "denied", "expired"]
    restart_required: bool
