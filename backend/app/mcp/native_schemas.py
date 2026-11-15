from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, assert_never

from pydantic import ConfigDict, Field, JsonValue, RootModel, StrictBool, model_validator

from app.identifiers import GoogleUserId, UserId
from app.mcp.identity import McpClientId
from app.mcp.schemas import McpCapability, McpModel
from app.models.mcp_grant import McpGrantId


class NativeOAuthError(ValueError):
    def __init__(self) -> None:
        super().__init__("Wiki authorization unavailable")


class WikiConsentIdentity(McpModel):
    user_id: UserId
    session_revision: int = Field(ge=0, strict=True)


class ConsentCommand(StrEnum):
    DENY = "deny"
    APPROVE_READ = "approve_read"
    APPROVE_TRANSFORM = "approve_transform"


class NativeAuthorization(McpModel):
    allowed: StrictBool


class _IntentClaims(McpModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")
    expires_at: datetime
    issuer: str
    client_id: McpClientId
    redirect_uri: str
    redirect_explicit: StrictBool
    resource: str
    state: str | None
    challenge: str = Field(min_length=1)
    scopes: tuple[McpCapability, ...]
    browser_binding: str = Field(min_length=1)


class UnboundIntent(_IntentClaims):
    kind: Literal["unbound"] = "unbound"


class _BoundIntentClaims(_IntentClaims):
    user_id: UserId
    session_revision: int = Field(ge=0, strict=True)
    csrf: str = Field(min_length=1)


class BoundIntent(_BoundIntentClaims):
    kind: Literal["bound"] = "bound"


class ApprovedIntent(_BoundIntentClaims):
    kind: Literal["approved"] = "approved"


class DeniedIntent(_BoundIntentClaims):
    kind: Literal["denied"] = "denied"


type NativeIntent = UnboundIntent | BoundIntent | ApprovedIntent | DeniedIntent


class _LegacyIntent(_IntentClaims):
    user_id: UserId | None = None
    session_revision: int | None = Field(default=None, ge=0, strict=True)
    csrf: str | None = Field(default=None, min_length=1)
    decided: StrictBool = False
    decision: Literal["approved", "denied"] | None = None


class NativeIntentPayload(RootModel[Annotated[NativeIntent, Field(discriminator="kind")]]):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def decode_legacy(
        cls, data: dict[str, JsonValue] | NativeIntent
    ) -> dict[str, JsonValue] | NativeIntent:
        if not isinstance(data, dict) or "kind" in data:
            return data
        old = _LegacyIntent.model_validate(data)
        binding = (old.user_id, old.session_revision, old.csrf)
        if any(value is None for value in binding) and any(value is not None for value in binding):
            msg = "Incomplete legacy consent identity binding"
            raise ValueError(msg)
        if old.decided != (old.decision is not None):
            msg = "Contradictory legacy consent decision"
            raise ValueError(msg)
        if old.user_id is None:
            if old.decided:
                msg = "Unbound legacy consent cannot be decided"
                raise ValueError(msg)
            return UnboundIntent.model_validate(
                old.model_dump(
                    exclude={"user_id", "session_revision", "csrf", "decided", "decision"}
                )
            )
        payload = old.model_dump(exclude={"decided", "decision"})
        match old.decision:
            case None:
                return BoundIntent.model_validate(payload)
            case "approved":
                return ApprovedIntent.model_validate(payload)
            case "denied":
                return DeniedIntent.model_validate(payload)
            case _:
                assert_never(old.decision)


def bind_intent(intent: UnboundIntent, identity: WikiConsentIdentity, csrf: str) -> BoundIntent:
    intent = UnboundIntent.model_validate(intent)
    return BoundIntent.model_validate(
        {
            **intent.model_dump(exclude={"kind"}),
            "user_id": identity.user_id,
            "session_revision": identity.session_revision,
            "csrf": csrf,
        }
    )


def settle_intent(intent: BoundIntent, command: ConsentCommand) -> ApprovedIntent | DeniedIntent:
    intent = BoundIntent.model_validate(intent)
    payload = intent.model_dump(exclude={"kind"})
    match command:
        case ConsentCommand.DENY:
            return DeniedIntent.model_validate(payload)
        case ConsentCommand.APPROVE_READ | ConsentCommand.APPROVE_TRANSFORM:
            return ApprovedIntent.model_validate(payload)
        case _:
            assert_never(command)


def intent_status(intent: NativeIntent) -> Literal["pending", "approved", "denied"]:
    match intent:
        case UnboundIntent() | BoundIntent():
            return "pending"
        case ApprovedIntent():
            return "approved"
        case DeniedIntent():
            return "denied"
        case _:
            assert_never(intent)


class _CredentialClaims(McpModel):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")
    issuer: str
    grant_id: McpGrantId
    client_id: McpClientId
    user_id: UserId
    google_subject: GoogleUserId
    issued_at: int = Field(ge=0, strict=True)
    expires_at: datetime
    resource: str
    scopes: tuple[McpCapability, ...]

    @model_validator(mode="after")
    def validate_scopes(self) -> Self:
        if McpCapability.READ not in self.scopes or len(set(self.scopes)) != len(self.scopes):
            msg = "Credential requires unique scopes including read"
            raise ValueError(msg)
        return self


class _CodeClaims(_CredentialClaims):
    challenge: str = Field(min_length=1)
    redirect_uri: str = Field(min_length=1)
    redirect_explicit: StrictBool


class ReadyCode(_CodeClaims):
    kind: Literal["ready_code"] = "ready_code"


class SpentCode(_CodeClaims):
    kind: Literal["spent_code"] = "spent_code"


class ActiveAccess(_CredentialClaims):
    kind: Literal["access"] = "access"


class ActiveRefresh(_CredentialClaims):
    kind: Literal["refresh"] = "refresh"


class SpentRefresh(_CredentialClaims):
    kind: Literal["spent_refresh"] = "spent_refresh"


type NativeCredential = ReadyCode | SpentCode | ActiveAccess | ActiveRefresh | SpentRefresh


class _LegacyCredential(_CredentialClaims):
    consumed: StrictBool = False
    challenge: str | None = None
    redirect_uri: str | None = None
    redirect_explicit: StrictBool = True


def _legacy_credential(
    data: dict[str, JsonValue] | NativeCredential, purpose: Literal["code", "access", "refresh"]
) -> dict[str, JsonValue] | NativeCredential:
    if not isinstance(data, dict) or "kind" in data:
        return data
    old = _LegacyCredential.model_validate(data)
    if purpose == "code":
        payload = old.model_dump(exclude={"consumed"})
        return (
            SpentCode.model_validate(payload) if old.consumed else ReadyCode.model_validate(payload)
        )
    if old.challenge is not None or old.redirect_uri is not None:
        msg = "Legacy token contains authorization-code binding"
        raise ValueError(msg)
    payload = old.model_dump(exclude={"consumed", "challenge", "redirect_uri", "redirect_explicit"})
    if purpose == "access":
        if old.consumed:
            msg = "Legacy access token cannot be consumed"
            raise ValueError(msg)
        return ActiveAccess.model_validate(payload)
    return (
        SpentRefresh.model_validate(payload)
        if old.consumed
        else ActiveRefresh.model_validate(payload)
    )


class CodePayload(RootModel[Annotated[ReadyCode | SpentCode, Field(discriminator="kind")]]):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def decode_legacy(
        cls, data: dict[str, JsonValue] | NativeCredential
    ) -> dict[str, JsonValue] | NativeCredential:
        return _legacy_credential(data, "code")


class AccessPayload(RootModel[ActiveAccess]):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def decode_legacy(
        cls, data: dict[str, JsonValue] | NativeCredential
    ) -> dict[str, JsonValue] | NativeCredential:
        return _legacy_credential(data, "access")


class RefreshPayload(
    RootModel[Annotated[ActiveRefresh | SpentRefresh, Field(discriminator="kind")]]
):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def decode_legacy(
        cls, data: dict[str, JsonValue] | NativeCredential
    ) -> dict[str, JsonValue] | NativeCredential:
        return _legacy_credential(data, "refresh")


def spend_code(code: ReadyCode) -> SpentCode:
    code = ReadyCode.model_validate(code)
    return SpentCode.model_validate(code.model_dump(exclude={"kind"}))


def spend_refresh(refresh: ActiveRefresh) -> SpentRefresh:
    refresh = ActiveRefresh.model_validate(refresh)
    return SpentRefresh.model_validate(refresh.model_dump(exclude={"kind"}))


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


class PendingConsentStatus(McpModel):
    status: Literal["pending"]
    restart_required: Literal[False] = False


class TerminalConsentStatus(McpModel):
    status: Literal["approved", "denied", "expired"]
    restart_required: Literal[True] = True


type ConsentStatus = Annotated[
    PendingConsentStatus | TerminalConsentStatus, Field(discriminator="status")
]


def consent_status_view(
    status: Literal["pending", "approved", "denied", "expired"],
) -> ConsentStatus:
    match status:
        case "pending":
            return PendingConsentStatus(status=status)
        case "approved" | "denied" | "expired":
            return TerminalConsentStatus(status=status)
        case _:
            assert_never(status)
