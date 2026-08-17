"""Safely inventory, plan, apply, and roll back explicit Google My Drive moves."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import csv
from datetime import UTC, datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Self, cast, override
from urllib.parse import parse_qs, urlsplit
import uuid
from wsgiref.simple_server import WSGIRequestHandler, make_server

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
import google_auth_httplib2
from google_auth_oauthlib.flow import Flow
from googleapiclient import discovery
from googleapiclient.errors import HttpError
import httplib2
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings
from app.drive.retry import execute_mutation, execute_with_retry
from app.drive.schemas import MIME_FOLDER
from app.drive.service import drive_discovery_document


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from googleapiclient._apis.drive.v3.resources import DriveResource
    from googleapiclient._apis.drive.v3.schemas import File


SCHEMA_VERSION = 1
HTTP_BAD_REQUEST = 400
HTTP_CONFLICT = 409
CONTENT_SIGNAL_THRESHOLD = 2
AUTO_MOVE_CONFIDENCE = 0.8
MIME_DOCUMENT = "application/vnd.google-apps.document"
DEFAULT_OUTPUT_DIR = Path(".drive-organizer")
DEFAULT_TOKEN_PATH = DEFAULT_OUTPUT_DIR / "oauth-token.json"
OAUTH_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive",
)
DRIVE_HTTP_TIMEOUT_SECONDS = 30
ROOT_FIELDS = (
    "id,name,mimeType,parents,driveId,ownedByMe,shared,trashed,createdTime,modifiedTime,"
    "size,version,capabilities(canMoveItemWithinDrive,canAddChildren),"
    "permissions(id,type,role,emailAddress,domain,allowFileDiscovery,expirationTime,deleted,pendingOwner),"
    "shortcutDetails(targetId,targetMimeType,targetResourceKey)"
)
LIST_FIELDS = f"nextPageToken,incompleteSearch,files({ROOT_FIELDS})"
TAXONOMY_PATHS = (
    "Work",
    "Work/Customers & Partners",
    "Work/Product & Engineering",
    "Work/Company Operations",
    "Work/Finance Legal & Compliance",
    "Work/Brand & Marketing",
    "Personal",
    "Personal/Identity & Immigration",
    "Personal/Finance",
    "Personal/Health & Relationships",
    "Personal/Security & Recovery",
    "Archive",
    "Archive/Work",
    "Archive/Personal",
    "Review",
)
TOP_LEVEL_TAXONOMY = frozenset({"Work", "Personal", "Archive", "Review"})
CLASSIFICATION_RULES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    (
        "Personal/Security & Recovery",
        ("recovery code", "backup code", "password", "credential", "secret key"),
        0.99,
    ),
    (
        "Personal/Identity & Immigration",
        ("visa", "passport", "immigration", "residency", "cv", "resume"),
        0.97,
    ),
    (
        "Personal/Health & Relationships",
        ("medical", "health", "insurance appeal", "relationship"),
        0.96,
    ),
    (
        "Work/Finance Legal & Compliance",
        (
            "reimbursement",
            "profit loss",
            "financial statement",
            "contract",
            "agreement",
            "nda",
            "audit",
            "security",
            "assessment",
            "questionnaire",
            "compliance",
            "due diligence",
            "company registration",
        ),
        0.92,
    ),
    (
        "Work/Customers & Partners",
        (
            "vision bank",
            "alinma",
            "ajb",
            "maqsam",
            "mrsool",
            "telnyx",
            "dabdoob",
            "khazna",
            "aamar",
            "gathern",
            "rtc league",
            "sifi",
            "wasalt",
            "etimad",
            "oracle",
            "yelo",
            "customer",
            "partner",
        ),
        0.9,
    ),
    (
        "Work/Product & Engineering",
        (
            "prd",
            "api",
            "architecture",
            "roadmap",
            "sla",
            "slo",
            "codebase",
            "developer setup",
            "stt",
            "voice",
            "linear issue",
            "linear project",
            "linear initiative",
            "technology",
        ),
        0.9,
    ),
    (
        "Work/Company Operations",
        (
            "onboarding",
            "promotion",
            "recruiting",
            "employee",
            "team building",
            "leave policy",
            "laptop remote",
            "fireflies",
            "meeting notes",
            "project kickoff",
        ),
        0.88,
    ),
    (
        "Work/Brand & Marketing",
        (
            "brand",
            "logo",
            "marketing",
            "landing page",
            "website",
            "sarj profile",
            "team spotlight",
            "founder image",
        ),
        0.88,
    ),
    (
        "Personal/Finance",
        ("estatement", "personal bank", "personal tax"),
        0.9,
    ),
)
CONTENT_CLASSIFICATION_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Personal/Identity & Immigration", ("passport", "visa", "residency", "immigration")),
    ("Personal/Health & Relationships", ("medical", "health", "insurance", "relationship")),
    ("Work/Company Operations", ("employee", "performance", "recruiting", "onboarding")),
    ("Work/Product & Engineering", ("architecture", "api", "engineering", "codebase")),
    ("Work/Finance Legal & Compliance", ("contract", "compliance", "audit", "security")),
)


class CapabilitySnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    can_move_item_within_drive: bool = Field(default=False, alias="canMoveItemWithinDrive")
    can_add_children: bool = Field(default=False, alias="canAddChildren")


class PermissionSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    type: str
    role: str
    email_address: str | None = Field(default=None, alias="emailAddress")
    domain: str | None = None
    allow_file_discovery: bool | None = Field(default=None, alias="allowFileDiscovery")
    expiration_time: str | None = Field(default=None, alias="expirationTime")
    deleted: bool | None = None
    pending_owner: bool | None = Field(default=None, alias="pendingOwner")


class InventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    mime_type: str
    parent_ids: list[str]
    drive_id: str | None = None
    owned_by_me: bool
    shared: bool
    trashed: bool
    created_time: str | None = None
    modified_time: str | None = None
    size: int | None = None
    version: str | None = None
    capabilities: CapabilitySnapshot
    permissions: list[PermissionSnapshot]
    permission_fingerprint: str
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None
    path: str
    depth: int
    root_child_id: str

    @property
    def is_folder(self) -> bool:
        return self.mime_type == MIME_FOLDER


class InventorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    generated_at: str
    account_email: str
    root_id: str
    root_item_count: int
    complete: bool
    errors: list[str]
    items: list[InventoryItem]


ManifestAction = Literal["MOVE", "KEEP", "REVIEW", "SKIP"]


class _ManifestActionResult(NamedTuple):
    action: ManifestAction
    reason: str


class ManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str
    name: str
    mime_type: str
    current_parent_id: str | None
    destination_path: str | None
    action: ManifestAction
    confidence: float = Field(ge=0, le=1)
    reason: str
    shared: bool
    subtree_shared: bool
    subtree_owned: bool
    subtree_complete: bool
    permission_risk_accepted: bool = False

    @model_validator(mode="after")
    def move_has_destination(self) -> Self:
        if self.action == "MOVE" and self.destination_path is None:
            msg = "MOVE entries require destination_path"
            raise ValueError(msg)
        if self.destination_path is not None and self.destination_path not in TAXONOMY_PATHS:
            msg = f"Unsupported destination path: {self.destination_path}"
            raise ValueError(msg)
        return self


class MoveManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    generated_at: str
    account_email: str
    root_id: str
    inventory_sha256: str
    taxonomy_paths: list[str]
    items: list[ManifestItem]

    @field_validator("taxonomy_paths")
    @classmethod
    def taxonomy_is_allowed(cls, paths: list[str]) -> list[str]:
        if paths != list(TAXONOMY_PATHS):
            msg = "taxonomy_paths must exactly match the supported taxonomy"
            raise ValueError(msg)
        return paths


class Classification(BaseModel):
    destination_path: str | None
    confidence: float
    reason: str


class _QuietOAuthRequestHandler(WSGIRequestHandler):
    @override
    def log_message(self, format: str, *args: object) -> None:
        del format
        del args


class DriveOrganizer:
    def __init__(self, client: DriveResource, account_email: str) -> None:
        self._client = client
        self._account_email = account_email.lower()

    def inventory(self) -> InventorySnapshot:  # ruff: ignore[too-many-locals] — explicit state.
        self.verify_identity()
        root = self._get_file("root")
        root_id = _required_string(root, "id")
        items: dict[str, InventoryItem] = {}
        errors: list[str] = []
        queue: deque[tuple[str, str, str | None]] = deque([(root_id, "", None)])
        visited_folders: set[str] = set()

        while queue:
            folder_id, parent_path, inherited_root_child = queue.popleft()
            if folder_id in visited_folders:
                continue
            visited_folders.add(folder_id)
            try:
                children = self._list_children(folder_id)
            except HttpError as exc:
                errors.append(f"folder {folder_id}: HTTP {exc.resp.status}")
                continue
            for raw in children:
                item_id = _required_string(raw, "id")
                name = _required_string(raw, "name")
                path = f"{parent_path}/{name}" if parent_path else name
                root_child_id = inherited_root_child or item_id
                item = _inventory_item(raw, path=path, root_child_id=root_child_id)
                existing = items.get(item_id)
                if existing is not None:
                    if existing.path != path:
                        errors.append(f"item {item_id} appeared at multiple paths")
                    continue
                items[item_id] = item
                if item.is_folder and item.drive_id is None:
                    queue.append((item.id, item.path, root_child_id))

        ordered = sorted(items.values(), key=lambda item: (item.path.casefold(), item.id))
        return InventorySnapshot(
            generated_at=_now(),
            account_email=self._account_email,
            root_id=root_id,
            root_item_count=sum(root_id in item.parent_ids for item in ordered),
            complete=not errors,
            errors=errors,
            items=ordered,
        )

    def verify_identity(self) -> None:
        raw = cast("dict[str, Any]", execute_with_retry(self._client.about().get(fields="user")))
        user = raw.get("user")
        email = user.get("emailAddress") if isinstance(user, dict) else None
        if not isinstance(email, str) or email.lower() != self._account_email:
            msg = f"Authenticated Drive account is {email!r}, expected {self._account_email!r}"
            raise RuntimeError(msg)

    def verify_root(self, expected_root_id: str) -> None:
        live_root_id = _required_string(self._get_file("root"), "id")
        if live_root_id != expected_root_id:
            msg = f"Live My Drive root is {live_root_id!r}, expected {expected_root_id!r}"
            raise RuntimeError(msg)

    def preflight(self, manifest: MoveManifest, inventory: InventorySnapshot) -> None:
        """Validate identity, lineage, metadata, ownership, and permissions without mutations."""
        self.verify_identity()
        self.verify_root(manifest.root_id)
        if inventory.root_id != manifest.root_id:
            msg = "Manifest and inventory root IDs do not match"
            raise RuntimeError(msg)
        if inventory.account_email.lower() != manifest.account_email.lower():
            msg = "Manifest and inventory accounts do not match"
            raise RuntimeError(msg)
        if not inventory.complete:
            msg = "Refusing to apply from an incomplete inventory"
            raise RuntimeError(msg)
        self.validate_taxonomy(manifest.taxonomy_paths)
        inventory_by_id = {item.id: item for item in inventory.items}
        for entry in manifest.items:
            if entry.action != "MOVE":
                continue
            expected = inventory_by_id.get(entry.file_id)
            if expected is None or expected.root_child_id != entry.file_id:
                msg = f"MOVE entry {entry.file_id} is not a root item in the inventory"
                raise RuntimeError(msg)
            current = self._get_file(entry.file_id)
            self._validate_against_inventory(current, entry, expected)
            expected_permissions = self.expected_permission_fingerprints(entry, inventory)
            live_permissions = self._live_permission_fingerprints(entry)
            if live_permissions != expected_permissions:
                msg = f"Permission or subtree drift for {entry.file_id}"
                raise RuntimeError(msg)

    def validate_taxonomy(self, paths: Sequence[str]) -> None:
        """Read-only validation of any taxonomy folders that already exist."""
        root_id = _required_string(self._get_file("root"), "id")
        parent_ids: dict[str, str | None] = {"": root_id}
        for path in sorted(paths, key=lambda value: (value.count("/"), paths.index(value))):
            parent_path, _, name = path.rpartition("/")
            parent_id = parent_ids[parent_path]
            if parent_id is None:
                parent_ids[path] = None
                continue
            parent_ids[path] = self._find_private_folder(parent_id, name)

    @staticmethod
    def _validate_against_inventory(
        current: dict[str, Any],
        entry: ManifestItem,
        expected: InventoryItem,
    ) -> None:
        if (
            current.get("name") != entry.name
            or current.get("mimeType") != entry.mime_type
            or _string_list(current.get("parents")) != [entry.current_parent_id]
        ):
            msg = f"Metadata or parent drift for {entry.file_id}"
            raise RuntimeError(msg)
        _validate_movable(current, entry)
        if (
            permission_fingerprint(current.get("permissions", []))
            != expected.permission_fingerprint
        ):
            msg = f"Permission drift for {entry.file_id}"
            raise RuntimeError(msg)

    @staticmethod
    def expected_permission_fingerprints(
        entry: ManifestItem, inventory: InventorySnapshot
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        target_id = entry.file_id
        for item in inventory.items:
            if target_id in {item.id, item.root_child_id}:
                result[item.id] = item.permission_fingerprint
        return result

    def _live_permission_fingerprints(self, entry: ManifestItem) -> dict[str, str]:
        current = self._get_file(entry.file_id)
        if current.get("shared") is True and not entry.permission_risk_accepted:
            msg = f"Item {entry.file_id} became shared"
            raise RuntimeError(msg)
        result = {entry.file_id: permission_fingerprint(current.get("permissions", []))}
        if entry.mime_type != MIME_FOLDER:
            return result
        queue = deque([entry.file_id])
        visited: set[str] = set()
        while queue:
            folder_id = queue.popleft()
            if folder_id in visited:
                continue
            visited.add(folder_id)
            for child in self._list_children(folder_id):
                child_id = _required_string(child, "id")
                if child_id in result:
                    msg = f"Folder subtree contains duplicate item {child_id}"
                    raise RuntimeError(msg)
                if child.get("driveId") is not None or child.get("ownedByMe") is not True:
                    msg = f"Folder subtree contains out-of-scope or non-owned item {child_id}"
                    raise RuntimeError(msg)
                if child.get("shared") is True and not entry.permission_risk_accepted:
                    msg = f"Folder subtree contains shared item {child_id}"
                    raise RuntimeError(msg)
                result[child_id] = permission_fingerprint(child.get("permissions", []))
                if child.get("mimeType") == MIME_FOLDER:
                    queue.append(child_id)
        return result

    def sample_document(self, item: InventoryItem) -> str:
        if item.mime_type != MIME_DOCUMENT:
            return ""
        raw = execute_with_retry(self._client.files().export(fileId=item.id, mimeType="text/plain"))
        if isinstance(raw, bytes):
            return raw[:4096].decode("utf-8", errors="replace")
        return str(raw)[:4096]

    def ensure_taxonomy(
        self, paths: Sequence[str], *, run_id: str, journal: Path
    ) -> dict[str, str]:
        root_id = _required_string(self._get_file("root"), "id")
        resolved: dict[str, str] = {}
        parent_ids: dict[str, str] = {"": root_id}
        for path in sorted(paths, key=lambda value: (value.count("/"), paths.index(value))):
            parent_path, _, name = path.rpartition("/")
            parent_id = parent_ids[parent_path]
            folder_id = self._find_private_folder(parent_id, name)
            if folder_id is None:
                folder_id = self._create_folder(
                    parent_id=parent_id,
                    name=name,
                    path=path,
                    run_id=run_id,
                    journal=journal,
                )
            parent_ids[path] = folder_id
            resolved[path] = folder_id
        return resolved

    def apply_move(
        self,
        entry: ManifestItem,
        *,
        target_id: str,
        expected_permissions: dict[str, str],
        run_id: str,
        journal: Path,
    ) -> None:
        current = self._get_file(entry.file_id)
        parents = _string_list(current.get("parents"))
        if parents == [target_id]:
            _append_journal(
                journal,
                {"run_id": run_id, "event": "already_applied", "file_id": entry.file_id},
            )
            return
        if parents != [entry.current_parent_id]:
            msg = f"Parent conflict for {entry.file_id}: expected {entry.current_parent_id}, got {parents}"
            raise RuntimeError(msg)
        _validate_movable(current, entry)
        live_permissions = self._live_permission_fingerprints(entry)
        if live_permissions != expected_permissions:
            msg = f"Permission or subtree drift for {entry.file_id}"
            raise RuntimeError(msg)
        destination = self._get_file(target_id)
        _validate_destination(destination)
        before_permissions = live_permissions
        if (entry.shared or entry.subtree_shared) and not current.get("permissions"):
            msg = f"Cannot verify permissions for shared item {entry.file_id}"
            raise RuntimeError(msg)
        _append_journal(
            journal,
            {
                "run_id": run_id,
                "event": "intent",
                "file_id": entry.file_id,
                "old_parent_id": entry.current_parent_id,
                "new_parent_id": target_id,
                "mime_type": entry.mime_type,
                "permission_fingerprints": before_permissions,
            },
        )
        self._move_with_reconciliation(
            entry.file_id,
            old_parent_id=entry.current_parent_id,
            target_id=target_id,
        )
        after = self._get_file(entry.file_id)
        after_parents = _string_list(after.get("parents"))
        after_permissions = self._live_permission_fingerprints(entry)
        if after_parents != [target_id] or after_permissions != before_permissions:
            self._reverse_after_failed_verification(
                entry.file_id,
                old_parent_id=cast("str", entry.current_parent_id),
                target_id=target_id,
            )
            msg = f"Verification failed for {entry.file_id}; reverse move attempted"
            raise RuntimeError(msg)
        _append_journal(
            journal,
            {
                "run_id": run_id,
                "event": "success",
                "file_id": entry.file_id,
                "old_parent_id": entry.current_parent_id,
                "new_parent_id": target_id,
                "mime_type": entry.mime_type,
                "permission_fingerprints": after_permissions,
            },
        )

    def rollback_move(self, event: dict[str, Any], *, run_id: str, journal: Path) -> None:
        file_id = _required_string(event, "file_id")
        old_parent_id = _required_string(event, "old_parent_id")
        new_parent_id = _required_string(event, "new_parent_id")
        current = self._get_file(file_id)
        parents = _string_list(current.get("parents"))
        expected_permissions = event.get("permission_fingerprints")
        if not isinstance(expected_permissions, dict):
            msg = f"Rollback event for {file_id} lacks permission fingerprints"
            raise TypeError(msg)
        for expected_id, expected_fingerprint in expected_permissions.items():
            if not isinstance(expected_id, str) or not isinstance(expected_fingerprint, str):
                msg = f"Rollback event for {file_id} has invalid permission fingerprints"
                raise TypeError(msg)
            live = self._get_file(expected_id)
            if permission_fingerprint(live.get("permissions", [])) != expected_fingerprint:
                msg = f"Rollback permission conflict for {expected_id}"
                raise RuntimeError(msg)
        if parents == [old_parent_id]:
            return
        if parents != [new_parent_id]:
            msg = f"Rollback conflict for {file_id}: expected {new_parent_id}, got {parents}"
            raise RuntimeError(msg)
        self._move_with_reconciliation(
            file_id,
            old_parent_id=new_parent_id,
            target_id=old_parent_id,
        )
        verified = self._get_file(file_id)
        if _string_list(verified.get("parents")) != [old_parent_id]:
            msg = f"Rollback verification failed for {file_id}"
            raise RuntimeError(msg)
        for expected_id, expected_fingerprint in expected_permissions.items():
            live = self._get_file(expected_id)
            if permission_fingerprint(live.get("permissions", [])) != expected_fingerprint:
                msg = f"Rollback permission verification failed for {expected_id}"
                raise RuntimeError(msg)
        _append_journal(
            journal,
            {"run_id": run_id, "event": "rollback_success", "file_id": file_id},
        )

    def _list_children(self, folder_id: str) -> list[dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        page_token: str | None = None
        restarts = 0
        while True:
            params: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "corpora": "user",
                "spaces": "drive",
                "includeItemsFromAllDrives": False,
                "supportsAllDrives": False,
                "pageSize": 1000,
                "fields": LIST_FIELDS,
            }
            if page_token is not None:
                params["pageToken"] = page_token
            try:
                raw = cast(
                    "dict[str, Any]", execute_with_retry(self._client.files().list(**params))
                )
            except HttpError as exc:
                if page_token is None or exc.resp.status != HTTP_BAD_REQUEST or restarts >= 1:
                    raise
                page_token = None
                restarts += 1
                continue
            for item in raw.get("files", []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    results[item["id"]] = item
            if raw.get("incompleteSearch") is True:
                msg = f"Drive returned an incomplete search under folder {folder_id}"
                raise RuntimeError(msg)
            token = raw.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return list(results.values())
            page_token = token

    def _get_file(self, file_id: str) -> dict[str, Any]:
        return cast(
            "dict[str, Any]",
            execute_with_retry(
                self._client.files().get(
                    fileId=file_id,
                    fields=ROOT_FIELDS,
                    supportsAllDrives=False,
                )
            ),
        )

    def _find_private_folder(self, parent_id: str, name: str) -> str | None:
        escaped_name = name.replace("'", "\\'")
        params: dict[str, Any] = {
            "q": (
                f"'{parent_id}' in parents and name='{escaped_name}' and "
                f"mimeType='{MIME_FOLDER}' and trashed=false"
            ),
            "corpora": "user",
            "spaces": "drive",
            "includeItemsFromAllDrives": False,
            "supportsAllDrives": False,
            "pageSize": 10,
            "fields": LIST_FIELDS,
        }
        raw = cast("dict[str, Any]", execute_with_retry(self._client.files().list(**params)))
        if raw.get("incompleteSearch") is True or raw.get("nextPageToken"):
            msg = f"Drive returned an incomplete taxonomy lookup for {name!r}"
            raise RuntimeError(msg)
        matches = [item for item in raw.get("files", []) if isinstance(item, dict)]
        if not matches:
            return None
        if len(matches) != 1:
            msg = f"Multiple folders named {name!r} under parent {parent_id}"
            raise RuntimeError(msg)
        folder = matches[0]
        _validate_destination(folder)
        if bool(folder.get("shared")):
            msg = f"Existing taxonomy folder {name!r} is shared"
            raise RuntimeError(msg)
        return _required_string(folder, "id")

    def _create_folder(
        self,
        *,
        parent_id: str,
        name: str,
        path: str,
        run_id: str,
        journal: Path,
    ) -> str:
        raw_ids = cast(
            "dict[str, Any]",
            execute_with_retry(self._client.files().generateIds(count=1, space="drive")),
        )
        ids = _string_list(raw_ids.get("ids"))
        if len(ids) != 1:
            msg = "Drive did not return a pre-generated folder ID"
            raise RuntimeError(msg)
        folder_id = ids[0]
        _append_journal(
            journal,
            {
                "run_id": run_id,
                "event": "folder_create_intent",
                "folder_id": folder_id,
                "parent_id": parent_id,
                "path": path,
            },
        )
        body: File = {
            "id": folder_id,
            "name": name,
            "mimeType": MIME_FOLDER,
            "parents": [parent_id],
            "appProperties": {"driveOrganizerRun": run_id},
        }
        try:
            execute_mutation(
                self._client.files().create(
                    body=body,
                    fields=ROOT_FIELDS,
                    supportsAllDrives=False,
                )
            )
        except HttpError as exc:
            if exc.resp.status != HTTP_CONFLICT:
                try:
                    existing = self._get_file(folder_id)
                except HttpError:
                    raise exc from None
                _validate_created_folder(existing, name=name, parent_id=parent_id)
            else:
                existing = self._get_file(folder_id)
                _validate_created_folder(existing, name=name, parent_id=parent_id)
        created = self._get_file(folder_id)
        _validate_created_folder(created, name=name, parent_id=parent_id)
        _append_journal(
            journal,
            {"run_id": run_id, "event": "folder_created", "folder_id": folder_id, "path": path},
        )
        return folder_id

    def _move_with_reconciliation(
        self,
        file_id: str,
        *,
        old_parent_id: str | None,
        target_id: str,
    ) -> None:
        if old_parent_id is None:
            msg = f"Cannot move {file_id} without an expected parent"
            raise RuntimeError(msg)
        try:
            execute_mutation(
                self._client.files().update(
                    fileId=file_id,
                    body={},
                    addParents=target_id,
                    removeParents=old_parent_id,
                    fields="id,parents,version",
                    supportsAllDrives=False,
                )
            )
        except HttpError:
            current = self._get_file(file_id)
            if _string_list(current.get("parents")) == [target_id]:
                return
            raise

    def _reverse_after_failed_verification(
        self, file_id: str, *, old_parent_id: str, target_id: str
    ) -> None:
        current = self._get_file(file_id)
        if _string_list(current.get("parents")) == [target_id]:
            self._move_with_reconciliation(
                file_id,
                old_parent_id=target_id,
                target_id=old_parent_id,
            )


def build_manifest(
    snapshot: InventorySnapshot,
    *,
    inventory_sha256: str,
    content_signals: dict[str, Classification] | None = None,
    move_private_review: bool = False,
) -> MoveManifest:
    content_signals = content_signals or {}
    descendants = _descendants_by_root(snapshot.items)
    items: list[ManifestItem] = []
    for item in snapshot.items:
        if snapshot.root_id not in item.parent_ids:
            continue
        subtree = descendants.get(item.id, [])
        subtree_owned = item.owned_by_me and all(child.owned_by_me for child in subtree)
        subtree_shared = item.shared or any(child.shared for child in subtree)
        subtree_complete = snapshot.complete and all(child.drive_id is None for child in subtree)
        classification = content_signals.get(item.id) or classify_item(item)
        action, reason = _manifest_action(
            item,
            classification,
            subtree_owned=subtree_owned,
            subtree_shared=subtree_shared,
            subtree_complete=subtree_complete,
        )
        if all(
            (
                move_private_review,
                action == "REVIEW",
                classification.destination_path == "Review",
                not subtree_shared,
                subtree_owned,
                subtree_complete,
            )
        ):
            action = "MOVE"
            reason = "Audited private ambiguous item staged in Review"
        items.append(
            ManifestItem(
                file_id=item.id,
                name=item.name,
                mime_type=item.mime_type,
                current_parent_id=item.parent_ids[0] if len(item.parent_ids) == 1 else None,
                destination_path=classification.destination_path,
                action=action,
                confidence=classification.confidence,
                reason=reason,
                shared=item.shared,
                subtree_shared=subtree_shared,
                subtree_owned=subtree_owned,
                subtree_complete=subtree_complete,
            )
        )
    return MoveManifest(
        generated_at=_now(),
        account_email=snapshot.account_email,
        root_id=snapshot.root_id,
        inventory_sha256=inventory_sha256,
        taxonomy_paths=list(TAXONOMY_PATHS),
        items=sorted(items, key=lambda entry: (entry.name.casefold(), entry.file_id)),
    )


def classify_item(
    item: InventoryItem, *, content_signal: Classification | None = None
) -> Classification:
    normalized = _normalize(item.name)
    if item.is_folder and item.name in TOP_LEVEL_TAXONOMY:
        return Classification(
            destination_path=None, confidence=1, reason="Existing taxonomy folder"
        )

    for destination, terms, confidence in CLASSIFICATION_RULES:
        if any(term in normalized for term in terms):
            return Classification(
                destination_path=destination,
                confidence=confidence,
                reason=f"Title metadata maps to {destination}",
            )
    if content_signal is not None:
        return content_signal
    return Classification(
        destination_path="Review",
        confidence=0.2,
        reason="Insufficient metadata for a safe classification",
    )


def classify_content(text: str) -> Classification | None:
    normalized = _normalize(text[:4096])
    for destination, terms in CONTENT_CLASSIFICATION_SIGNALS:
        if sum(term in normalized for term in terms) >= CONTENT_SIGNAL_THRESHOLD:
            return Classification(
                destination_path=destination,
                confidence=0.72,
                reason=f"Ephemeral in-memory content signals map to {destination}",
            )
    return None


async def load_drive_organizer(email: str) -> DriveOrganizer:
    client = await asyncio.to_thread(_local_drive_client, DEFAULT_TOKEN_PATH)
    return DriveOrganizer(client, email)


async def run_inventory(args: argparse.Namespace) -> None:
    organizer = await load_drive_organizer(cast("str", args.user_email))
    snapshot = await asyncio.to_thread(organizer.inventory)
    output_dir = _secure_output_dir(Path(cast("str", args.output_dir)))
    json_path = output_dir / "inventory.json"
    _write_model(json_path, snapshot)
    _write_inventory_csv(output_dir / "inventory.csv", snapshot)
    print(f"Inventoried {len(snapshot.items)} items ({snapshot.root_item_count} at root)")
    print(f"Inventory: {json_path}")
    if not snapshot.complete:
        print(f"WARNING: inventory incomplete ({len(snapshot.errors)} folder errors)")


async def run_plan(args: argparse.Namespace) -> None:
    inventory_path = Path(cast("str", args.inventory))
    snapshot = await asyncio.to_thread(_read_inventory, inventory_path)
    content_signals: dict[str, Classification] = {}
    if bool(args.sample_ambiguous_docs):
        organizer = await load_drive_organizer(snapshot.account_email)
        organizer.verify_identity()
        for item in snapshot.items:
            if snapshot.root_id not in item.parent_ids or item.mime_type != MIME_DOCUMENT:
                continue
            if classify_item(item).destination_path != "Review":
                continue
            text = await asyncio.to_thread(organizer.sample_document, item)
            if signal := classify_content(text):
                content_signals[item.id] = signal
    overrides_path = cast("str | None", args.overrides)
    if overrides_path is not None:
        content_signals.update(
            await asyncio.to_thread(_read_classification_overrides, Path(overrides_path))
        )
    manifest = build_manifest(
        snapshot,
        inventory_sha256=_sha256(inventory_path),
        content_signals=content_signals,
        move_private_review=bool(args.move_private_review),
    )
    output_dir = _secure_output_dir(Path(cast("str", args.output_dir)))
    path = output_dir / "draft-manifest.json"
    _write_model(path, manifest)
    counts: dict[str, int] = {}
    for manifest_item in manifest.items:
        counts[manifest_item.action] = counts.get(manifest_item.action, 0) + 1
    print(f"Manifest: {path}")
    print("Decisions: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


async def run_apply(args: argparse.Namespace) -> None:
    manifest_path = Path(cast("str", args.manifest))
    inventory_path = Path(cast("str", args.inventory))
    manifest = await asyncio.to_thread(_read_manifest, manifest_path)
    inventory = await asyncio.to_thread(_read_inventory, inventory_path)
    if not hmac.compare_digest(_sha256(inventory_path), manifest.inventory_sha256):
        msg = "Inventory hash does not match the manifest lineage"
        raise RuntimeError(msg)
    confirmation = cast("str", args.confirm_account).lower()
    if confirmation != manifest.account_email.lower():
        msg = "--confirm-account must exactly match the manifest account"
        raise RuntimeError(msg)
    organizer = await load_drive_organizer(confirmation)
    await asyncio.to_thread(organizer.preflight, manifest, inventory)
    run_id = str(uuid.uuid4())
    output_dir = _secure_output_dir(Path(cast("str", args.output_dir)))
    journal = output_dir / "execution-journal.jsonl"
    _append_journal(
        journal,
        {
            "run_id": run_id,
            "event": "run_start",
            "manifest_path": str(manifest_path),
            "manifest_sha256": _sha256(manifest_path),
            "account_email": manifest.account_email,
        },
    )
    targets = await asyncio.to_thread(
        organizer.ensure_taxonomy,
        manifest.taxonomy_paths,
        run_id=run_id,
        journal=journal,
    )
    for entry in manifest.items:
        if entry.action != "MOVE":
            continue
        if entry.destination_path is None:
            msg = f"MOVE entry {entry.file_id} has no destination"
            raise RuntimeError(msg)
        await asyncio.to_thread(
            organizer.apply_move,
            entry,
            target_id=targets[entry.destination_path],
            expected_permissions=organizer.expected_permission_fingerprints(entry, inventory),
            run_id=run_id,
            journal=journal,
        )
    print(f"Apply complete. Journal: {journal}")


async def run_audit(args: argparse.Namespace) -> None:
    manifest_path = Path(cast("str", args.manifest))
    inventory_path = Path(cast("str", args.inventory))
    manifest = await asyncio.to_thread(_read_manifest, manifest_path)
    inventory = await asyncio.to_thread(_read_inventory, inventory_path)
    if not hmac.compare_digest(_sha256(inventory_path), manifest.inventory_sha256):
        msg = "Inventory hash does not match the manifest lineage"
        raise RuntimeError(msg)
    confirmation = cast("str", args.confirm_account).lower()
    if confirmation != manifest.account_email.lower():
        msg = "--confirm-account must exactly match the manifest account"
        raise RuntimeError(msg)
    organizer = await load_drive_organizer(confirmation)
    await asyncio.to_thread(organizer.preflight, manifest, inventory)
    move_count = sum(entry.action == "MOVE" for entry in manifest.items)
    print(f"Read-only audit passed for {move_count} proposed moves; Drive was not changed")


async def run_rollback(args: argparse.Namespace) -> None:
    journal = Path(cast("str", args.journal))
    events = _read_journal(journal)
    starts = [event for event in events if event.get("event") == "run_start"]
    if not starts:
        msg = "Journal contains no run_start event"
        raise RuntimeError(msg)
    account = _required_string(starts[-1], "account_email")
    confirmation = cast("str", args.confirm_account).lower()
    if confirmation != account.lower():
        msg = "--confirm-account must exactly match the journal account"
        raise RuntimeError(msg)
    organizer = await load_drive_organizer(confirmation)
    organizer.verify_identity()
    rollback_run_id = f"rollback-{uuid.uuid4()}"
    source_run_id = _required_string(starts[-1], "run_id")
    successes = [
        event
        for event in events
        if event.get("event") == "success" and event.get("run_id") == source_run_id
    ]
    await asyncio.to_thread(
        _rollback_events,
        organizer,
        successes,
        run_id=rollback_run_id,
        journal=journal,
    )
    print(f"Rollback complete. Created taxonomy folders were retained. Journal: {journal}")


def _rollback_events(
    organizer: DriveOrganizer,
    events: Sequence[dict[str, Any]],
    *,
    run_id: str,
    journal: Path,
) -> None:
    for event in reversed(events):
        organizer.rollback_move(event, run_id=run_id, journal=journal)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="Read all My Drive metadata")
    inventory.add_argument("--user-email", required=True)

    plan = subparsers.add_parser("plan", help="Create a read-only proposed move manifest")
    plan.add_argument("--inventory", required=True)
    plan.add_argument("--sample-ambiguous-docs", action="store_true")
    plan.add_argument("--overrides", help="Audited JSON map of file IDs to destinations")
    plan.add_argument(
        "--move-private-review",
        action="store_true",
        help="Move fully owned, unshared ambiguous root items into Review",
    )

    apply = subparsers.add_parser("apply", help="Apply an explicitly reviewed manifest")
    apply.add_argument("--manifest", required=True)
    apply.add_argument("--inventory", required=True)
    apply.add_argument("--confirm-account", required=True)

    audit = subparsers.add_parser("audit", help="Run the complete apply preflight read-only")
    audit.add_argument("--manifest", required=True)
    audit.add_argument("--inventory", required=True)
    audit.add_argument("--confirm-account", required=True)

    rollback = subparsers.add_parser("rollback", help="Reverse successful journaled moves")
    rollback.add_argument("--journal", required=True)
    rollback.add_argument("--confirm-account", required=True)
    return parser


async def async_main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "inventory":
        await run_inventory(args)
    elif args.command == "plan":
        await run_plan(args)
    elif args.command == "audit":
        await run_audit(args)
    elif args.command == "apply":
        await run_apply(args)
    else:
        await run_rollback(args)


def main() -> None:
    asyncio.run(async_main())


def _inventory_item(raw: dict[str, Any], *, path: str, root_child_id: str) -> InventoryItem:
    permissions = [
        PermissionSnapshot.model_validate(permission)
        for permission in raw.get("permissions", [])
        if isinstance(permission, dict)
    ]
    shortcut = raw.get("shortcutDetails")
    shortcut = shortcut if isinstance(shortcut, dict) else {}
    return InventoryItem(
        id=_required_string(raw, "id"),
        name=_required_string(raw, "name"),
        mime_type=_required_string(raw, "mimeType"),
        parent_ids=_string_list(raw.get("parents")),
        drive_id=raw.get("driveId") if isinstance(raw.get("driveId"), str) else None,
        owned_by_me=raw.get("ownedByMe") is True,
        shared=raw.get("shared") is True,
        trashed=raw.get("trashed") is True,
        created_time=raw.get("createdTime") if isinstance(raw.get("createdTime"), str) else None,
        modified_time=(
            raw.get("modifiedTime") if isinstance(raw.get("modifiedTime"), str) else None
        ),
        size=_optional_int(raw.get("size")),
        version=str(raw["version"]) if raw.get("version") is not None else None,
        capabilities=CapabilitySnapshot.model_validate(raw.get("capabilities", {})),
        permissions=permissions,
        permission_fingerprint=permission_fingerprint(raw.get("permissions", [])),
        shortcut_target_id=(
            shortcut.get("targetId") if isinstance(shortcut.get("targetId"), str) else None
        ),
        shortcut_target_mime_type=(
            shortcut.get("targetMimeType")
            if isinstance(shortcut.get("targetMimeType"), str)
            else None
        ),
        path=path,
        depth=path.count("/") + 1,
        root_child_id=root_child_id,
    )


def _manifest_action(
    item: InventoryItem,
    classification: Classification,
    *,
    subtree_owned: bool,
    subtree_shared: bool,
    subtree_complete: bool,
) -> _ManifestActionResult:
    if classification.destination_path is None:
        return _ManifestActionResult("KEEP", classification.reason)
    if item.drive_id is not None:
        return _ManifestActionResult(
            "SKIP", "Shared Drive item discovered inside the My Drive graph"
        )
    if item.trashed:
        return _ManifestActionResult("SKIP", "Trashed item")
    if len(item.parent_ids) != 1:
        return _ManifestActionResult("SKIP", "Item does not have exactly one parent")
    if not item.owned_by_me or not item.capabilities.can_move_item_within_drive:
        return _ManifestActionResult("SKIP", "Item is not owned by the user or cannot be moved")
    if item.is_folder and (not subtree_complete or not subtree_owned):
        return _ManifestActionResult(
            "REVIEW", "Folder subtree is incomplete or contains non-owned items"
        )
    if not subtree_complete:
        return _ManifestActionResult("REVIEW", "Inventory is incomplete; no automatic move is safe")
    if subtree_shared:
        return _ManifestActionResult(
            "REVIEW", "Shared item or subtree requires explicit permission-risk acceptance"
        )
    if (
        classification.destination_path == "Review"
        or classification.confidence < AUTO_MOVE_CONFIDENCE
    ):
        return _ManifestActionResult("REVIEW", classification.reason)
    return _ManifestActionResult("MOVE", classification.reason)


def _descendants_by_root(items: Iterable[InventoryItem]) -> dict[str, list[InventoryItem]]:
    descendants: dict[str, list[InventoryItem]] = {}
    for item in items:
        if item.id == item.root_child_id:
            continue
        descendants.setdefault(item.root_child_id, []).append(item)
    return descendants


def _validate_movable(raw: dict[str, Any], entry: ManifestItem) -> None:
    capabilities = CapabilitySnapshot.model_validate(raw.get("capabilities", {}))
    if raw.get("driveId") is not None or raw.get("trashed") is True:
        msg = f"Item {entry.file_id} is outside safe My Drive scope"
        raise RuntimeError(msg)
    if raw.get("ownedByMe") is not True or not capabilities.can_move_item_within_drive:
        msg = f"Item {entry.file_id} is not owned and movable"
        raise RuntimeError(msg)
    if (entry.shared or entry.subtree_shared) and not entry.permission_risk_accepted:
        msg = f"Item {entry.file_id} requires explicit permission-risk acceptance"
        raise RuntimeError(msg)
    if entry.mime_type == MIME_FOLDER and (not entry.subtree_complete or not entry.subtree_owned):
        msg = f"Folder {entry.file_id} did not pass subtree safety gates"
        raise RuntimeError(msg)


def _validate_destination(raw: dict[str, Any]) -> None:
    capabilities = CapabilitySnapshot.model_validate(raw.get("capabilities", {}))
    if raw.get("mimeType") != MIME_FOLDER or raw.get("trashed") is True:
        msg = "Destination is not an active folder"
        raise RuntimeError(msg)
    if raw.get("driveId") is not None or raw.get("ownedByMe") is not True:
        msg = "Destination is not an owned My Drive folder"
        raise RuntimeError(msg)
    if raw.get("shared") is True or not capabilities.can_add_children:
        msg = "Destination must be private and writable"
        raise RuntimeError(msg)


def _validate_created_folder(raw: dict[str, Any], *, name: str, parent_id: str) -> None:
    if raw.get("name") != name or _string_list(raw.get("parents")) != [parent_id]:
        msg = f"Pre-generated folder ID resolved to unexpected metadata for {name!r}"
        raise RuntimeError(msg)
    _validate_destination(raw)


def permission_fingerprint(raw_permissions: object) -> str:
    normalized: list[dict[str, object]] = []
    if isinstance(raw_permissions, list):
        for permission in raw_permissions:
            if not isinstance(permission, dict):
                continue
            normalized.append(
                {
                    key: permission.get(key)
                    for key in (
                        "id",
                        "type",
                        "role",
                        "emailAddress",
                        "domain",
                        "allowFileDiscovery",
                    )
                }
            )
    normalized.sort(key=lambda item: str(item.get("id", "")))
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _secure_output_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _write_model(path: Path, model: BaseModel) -> None:
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _write_inventory_csv(path: Path, snapshot: InventorySnapshot) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "id",
                "path",
                "mime_type",
                "parent_ids",
                "owned_by_me",
                "shared",
                "drive_id",
                "modified_time",
                "size",
                "permission_fingerprint",
            ),
        )
        writer.writeheader()
        for item in snapshot.items:
            writer.writerow(
                {
                    "id": item.id,
                    "path": item.path,
                    "mime_type": item.mime_type,
                    "parent_ids": ",".join(item.parent_ids),
                    "owned_by_me": item.owned_by_me,
                    "shared": item.shared,
                    "drive_id": item.drive_id or "",
                    "modified_time": item.modified_time or "",
                    "size": item.size if item.size is not None else "",
                    "permission_fingerprint": item.permission_fingerprint,
                }
            )
    path.chmod(0o600)


def _append_journal(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {"timestamp": _now(), **event}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\n").encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_journal(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if isinstance(raw, dict):
            events.append(raw)
    return events


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_inventory(path: Path) -> InventorySnapshot:
    return InventorySnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def _read_manifest(path: Path) -> MoveManifest:
    return MoveManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _read_classification_overrides(path: Path) -> dict[str, Classification]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = "Classification overrides must be a JSON object"
        raise TypeError(msg)
    result: dict[str, Classification] = {}
    for file_id, destination in raw.items():
        if not isinstance(file_id, str) or not isinstance(destination, str):
            msg = "Classification override keys and values must be strings"
            raise TypeError(msg)
        if destination not in TAXONOMY_PATHS:
            msg = f"Unsupported override destination: {destination}"
            raise ValueError(msg)
        result[file_id] = Classification(
            destination_path=destination,
            confidence=1,
            reason="Read-only metadata audit decision",
        )
    return result


def _local_drive_client(token_path: Path) -> DriveResource:
    credentials = _local_credentials(token_path)
    authorized_http = google_auth_httplib2.AuthorizedHttp(
        credentials,
        http=httplib2.Http(timeout=DRIVE_HTTP_TIMEOUT_SECONDS),
    )
    build_from_document = cast(
        "Callable[..., object]",
        vars(discovery)["build_from_document"],
    )
    built = build_from_document(
        drive_discovery_document(),
        http=authorized_http,
    )
    return cast("DriveResource", built)


def _local_credentials(token_path: Path) -> Credentials:
    credentials: Credentials | None = None
    if token_path.exists():
        load_credentials = cast(
            "Callable[..., Credentials]",
            Credentials.from_authorized_user_file,
        )
        credentials = load_credentials(
            str(token_path),
            scopes=OAUTH_SCOPES,
        )
    if credentials is not None and credentials.expired and credentials.refresh_token:
        refresh_credentials = cast("Callable[[GoogleAuthRequest], None]", credentials.refresh)
        refresh_credentials(GoogleAuthRequest())
        _write_credentials(token_path, credentials)
    if credentials is None or not credentials.valid:
        credentials = _run_local_oauth()
        _write_credentials(token_path, credentials)
    return credentials


def _run_local_oauth() -> Credentials:
    redirect_uri = str(settings.GOOGLE_REDIRECT_URI)
    redirect = urlsplit(redirect_uri)
    if redirect.hostname not in {"localhost", "127.0.0.1"} or redirect.port is None:
        msg = f"GOOGLE_REDIRECT_URI must be a localhost URL with a port, got {redirect_uri}"
        raise RuntimeError(msg)
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=OAUTH_SCOPES)
    flow.redirect_uri = redirect_uri
    authorization_url, expected_state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    callback: dict[str, str] = {}

    def application(
        environ: dict[str, object],
        start_response: Callable[[str, list[tuple[str, str]]], object],
    ) -> list[bytes]:
        path = str(environ.get("PATH_INFO", ""))
        if path != redirect.path:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not found"]
        query = str(environ.get("QUERY_STRING", ""))
        callback["query"] = query
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"Google Drive authorization complete. You can close this tab."]

    print("Open this URL to authorize the local Drive organizer:", flush=True)
    print(authorization_url, flush=True)
    with make_server(
        "127.0.0.1",
        redirect.port,
        application,
        handler_class=_QuietOAuthRequestHandler,
    ) as server:
        server.handle_request()
    query = callback.get("query")
    if query is None:
        msg = "OAuth callback did not contain an authorization response"
        raise RuntimeError(msg)
    params = parse_qs(query)
    returned_state = params.get("state", [""])[0]
    code = params.get("code", [""])[0]
    if not code or not hmac.compare_digest(returned_state, expected_state):
        msg = "OAuth callback code or state was invalid"
        raise RuntimeError(msg)
    flow.fetch_token(code=code)
    return cast("Credentials", flow.credentials)


def _write_credentials(path: Path, credentials: Credentials) -> None:
    _secure_output_dir(path.parent)
    serialize_credentials = cast("Callable[[], str]", credentials.to_json)
    path.write_text(
        serialize_credentials(),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Missing required string field {key!r}"
        raise RuntimeError(msg)
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    main()
