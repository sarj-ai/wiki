from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
import hashlib
import json
import random
import re
import threading
from typing import TYPE_CHECKING, NamedTuple, NewType, override
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from google.api_core import gapic_v1
from google.auth.credentials import AnonymousCredentials
from google.cloud import tasks_v2
from googleapiclient import discovery
from httplib2 import DEFAULT_MAX_REDIRECTS, Response
from pydantic import BaseModel, JsonValue, RootModel, model_serializer
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformResult,
    TransformFinding,
    TransformMode,
    ValidationSummary,
)
from app.drive.schemas import (
    MIME_DOC,
    MIME_FOLDER,
    MIME_SHORTCUT,
    DriveNode,
    DriveSummary,
    DriveTree,
)
from app.drive.service import DriveService, GoogleDriveService
from app.identifiers import DriveId, FileId, GoogleUserId, IdempotencyKey, TaskId, UserId
from app.models.task import Task
from app.models.user import User
from app.tasks.schemas import TaskKind, TaskStatus
from app.templates.schemas import TemplateRead
from tests.support import dependency


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource

FIXTURE_TIME = datetime(2026, 10, 11, tzinfo=UTC)
SEED = 73011
SHARED = DriveSummary(id=DriveId("round7-shared"), name="Round 7 絵")
PERSONAL = DriveSummary(id=DriveId("round7-personal"), name="My Drive", is_personal=True)
FolderId = NewType("FolderId", str)
ParentFolderId = NewType("ParentFolderId", str)


class TransportResponse(NamedTuple):
    # googleapiclient consumes httplib2's response/content tuple protocol.
    response: Response
    content: bytes


def fixture_user() -> User:
    return User(
        id=UserId(uuid.uuid7()),
        email=f"round7-provider-{uuid.uuid7()}@example.com",
        google_id=GoogleUserId(str(uuid.uuid7())),
        token_version=0,
    )


class DriveTransport:
    def __init__(
        self,
        *,
        children: Mapping[str, list[dict[str, JsonValue]]] | None = None,
        targets: Mapping[str, dict[str, JsonValue] | int] | None = None,
        searches: Mapping[str, list[dict[str, JsonValue]]] | None = None,
        all_hits: list[dict[str, JsonValue]] | None = None,
    ) -> None:
        self.children = {
            key: json.dumps({"files": rows}, ensure_ascii=True).encode()
            for key, rows in (children or {}).items()
        }
        self.targets = targets or {}
        self.searches = searches or {}
        self.all_hits = all_hits or []
        self.calls: list[tuple[str, dict[str, list[str]]]] = []
        self.batch_sizes: list[int] = []
        self.factory_threads: set[int] = set()
        self.lock = threading.Lock()

    def factory(self, _user: User, _refresh_handler: object | None) -> DriveResource:
        with self.lock:
            self.factory_threads.add(threading.get_ident())
        client: DriveResource = discovery.build(
            "drive", "v3", http=dependency(self), static_discovery=True
        )
        return client

    def request(
        self,
        uri: str,
        method: str = "GET",
        body: str | bytes | None = None,
        headers: Mapping[str, str] | None = None,
        *,
        redirections: int = DEFAULT_MAX_REDIRECTS,
        connection_type: object | None = None,
    ) -> TransportResponse:
        if method == "POST" and "/batch/" in uri:
            assert body is not None
            assert headers is not None
            return self.batch_response(body, headers)
        parsed = urlsplit(uri)
        params = parse_qs(parsed.query)
        path = unquote(parsed.path)
        with self.lock:
            self.calls.append((path, params))
        if path.endswith("/files/root"):
            raw: JsonValue = {"id": PERSONAL.id}
        elif path.endswith("/drives"):
            raw = {"drives": [{"id": SHARED.id, "name": SHARED.name}]}
        elif path.endswith("/files"):
            query = params.get("q", [""])[0]
            match = re.search(r"'([^']+)' in parents", query)
            if match is not None:
                return TransportResponse(
                    response=Response({"status": "200"}),
                    content=self.children.get(match[1], b'{"files":[]}'),
                )
            corpus = params["corpora"][0]
            rows = (
                self.all_hits
                if corpus == "allDrives"
                else self.searches.get(params.get("driveId", [PERSONAL.id])[0], [])
            )
            raw = {"files": list[JsonValue](rows)}
        else:
            file_id = path.rsplit("/", 1)[-1]
            response = self.targets[file_id]
            if isinstance(response, int):
                return TransportResponse(
                    response=Response({"status": str(response)}),
                    content=b'{"error":{"message":"fixture"}}',
                )
            raw = response
        return TransportResponse(
            response=Response({"status": "200"}), content=json.dumps(raw).encode()
        )

    def batch_response(self, body: str | bytes, headers: Mapping[str, str]) -> TransportResponse:
        encoded = body.encode() if isinstance(body, str) else body
        content_type = next(
            value for key, value in headers.items() if key.lower() == "content-type"
        )
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + encoded
        )
        response_parts: list[bytes] = []
        for part in message.iter_parts():
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            target = payload.splitlines()[0].decode().split(" ")[1]
            response, content = self.request(target)
            response_parts.append(
                (
                    "--round7-response\r\nContent-Type: application/http\r\n"
                    f"Content-ID: {part['Content-ID']}\r\n\r\n"
                    f"HTTP/1.1 {response.status} Fixture\r\n"
                    "Content-Type: application/json\r\n\r\n"
                ).encode()
                + content
                + b"\r\n"
            )
        with self.lock:
            self.batch_sizes.append(len(response_parts))
        return TransportResponse(
            response=Response(
                {"status": "200", "content-type": "multipart/mixed; boundary=round7-response"}
            ),
            content=b"".join(response_parts) + b"--round7-response--\r\n",
        )


def google_drive(transport: DriveTransport, user: User | None = None) -> GoogleDriveService:
    return GoogleDriveService(user or fixture_user(), client_factory=transport.factory)


def folder(file_id: FolderId, name: str, parent_id: ParentFolderId | None) -> DriveNode:
    return DriveNode(
        id=FileId(file_id),
        name=name,
        mime_type=MIME_FOLDER,
        parent_id=FileId(parent_id) if parent_id is not None else None,
        modified_time="2026-10-11T00:00:00Z",
    )


def wide_tree(count: int = 10_000) -> DriveTree:
    folders = [
        folder(FolderId(f"category-{i}"), f"Category {i} 絵 Straße", None) for i in range(40)
    ]
    folders.extend(
        folder(
            FolderId(f"folder-{i}"),
            f"Plan {i % 29} 日本語 Budget",
            ParentFolderId(f"category-{i % 40}"),
        )
        for i in range(count)
    )
    random.Random(SEED).shuffle(folders)
    return DriveTree(drive_id=SHARED.id, folders=folders, files=[], files_complete=True)


def deep_tree(count: int = 10_000) -> DriveTree:
    folders = [
        folder(
            FolderId(f"level-{i}"),
            f"Level {i} Straße 日本語",
            ParentFolderId(f"level-{i - 1}") if i else None,
        )
        for i in range(9)
    ]
    folders.extend(
        folder(FolderId(f"leaf-{i}"), f"Leaf {i} 絵", ParentFolderId("level-8"))
        for i in range(count)
    )
    random.Random(SEED + 1).shuffle(folders)
    return DriveTree(drive_id=SHARED.id, folders=folders, files=[], files_complete=True)


def shortcut_rows(count: int = 4_000) -> list[dict[str, JsonValue]]:
    return [
        {
            "id": f"shortcut-{i}",
            "name": f"Shortcut {i} 絵",
            "mimeType": MIME_SHORTCUT,
            "shortcutDetails": {"targetId": f"target-{i % 8}", "targetMimeType": MIME_DOC},
        }
        for i in range(count)
    ]


def shortcut_targets() -> dict[str, dict[str, JsonValue] | int]:
    return {
        "target-0": {"id": "target-0", "driveId": SHARED.id},
        "target-1": {"id": "target-1"},
        "target-2": 403,
        "target-3": 404,
        "target-4": {"id": "target-4", "trashed": True},
        "target-5": {"id": "target-5", "driveId": SHARED.id},
        "target-6": {"id": "target-6"},
        "target-7": {"id": "target-7", "driveId": SHARED.id},
    }


class RecordingTaskClient(tasks_v2.CloudTasksAsyncClient):
    def __init__(self) -> None:
        super().__init__(credentials=dependency(AnonymousCredentials)())
        self.requests: list[tasks_v2.CreateTaskRequest] = []

    @override
    async def create_task(
        self,
        request: object | None = None,
        *,
        parent: str | None = None,
        task: tasks_v2.Task | None = None,
        retry: object = gapic_v1.method.DEFAULT,
        timeout: float | object = gapic_v1.method.DEFAULT,
        metadata: Sequence[tuple[str, str | bytes]] = (),
    ) -> tasks_v2.Task:
        assert request is None
        assert parent is not None
        assert task is not None
        self.requests.append(tasks_v2.CreateTaskRequest(parent=parent, task=task))
        return task


class ListPayload(BaseModel):
    value: str = "must remain an object"

    @model_serializer
    def serialize(self) -> list[str]:
        return [self.value]


class JsonObject(RootModel[dict[str, JsonValue]]):
    pass


def streaming_canonical_hash(payload: dict[str, JsonValue]) -> str:
    result = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    for part in encoder.iterencode(payload):
        result.update(part.encode())
    return result.hexdigest()


def manual_dispatch_body(task_id: object) -> bytes:
    return f'{{"task_id":"{task_id}"}}'.encode()


async def shared_visited_catalog(drive: DriveService) -> list[TemplateRead]:
    roots = await drive.list_named_root_folders(SHARED.id, name="Templates")
    visited: set[FileId] = set()
    templates: list[TemplateRead] = []
    for root in sorted(roots, key=lambda item: item.id):
        pending: deque[tuple[FileId, tuple[str, ...]]] = deque([(root.id, ())])
        while pending and len(visited) < 500:
            file_id, category = pending.popleft()
            if file_id in visited:
                continue
            visited.add(file_id)
            for child in await drive.list_template_children(file_id):
                if child.mime_type == MIME_FOLDER:
                    pending.append((child.id, (*category, child.name)))
                elif child.mime_type == MIME_DOC and child.modified_time is not None:
                    templates.append(
                        TemplateRead(
                            file_id=child.id,
                            drive_id=SHARED.id,
                            drive_name=SHARED.name,
                            name=child.name,
                            description=child.description,
                            mime_type="application/vnd.google-apps.document",
                            modified_time=child.modified_time,
                            category_path=list(category),
                        )
                    )
    return templates


def transform_result(task_id: TaskId, finding_count: int = 20) -> DocumentTransformResult:
    return DocumentTransformResult(
        operation_id=task_id,
        source_sha256="a" * 64,
        output=DocumentOutput(
            file_id=FileId("output"), name="Output 絵", parent_folder_id=FileId("parent")
        ),
        mode=TransformMode.BOTH,
        applied_patch_count=0,
        findings=[
            TransformFinding(code=f"finding-{i}", detail="Fixture detail 絵")
            for i in range(finding_count)
        ],
        validation=ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=True,
            structure_preserved=True,
            unsafe_patch_count=0,
        ),
        model_ids=[],
        tool_versions={"fixture": "round7"},
        pipeline_version="round7",
        needs_review=False,
    )


def succeeded_task(user: User, *, evidence: bool = False) -> Task:
    task_id = TaskId(uuid.uuid7())
    raw = transform_result(task_id).model_dump(mode="json")
    if evidence:
        raw["qa_permission_evidence"] = {"private": "round7 evidence must never be public"}
    return Task(
        id=task_id,
        owner_user_id=user.id,
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        status=TaskStatus.SUCCEEDED,
        idempotency_key=IdempotencyKey(uuid.uuid7()),
        request_hash="a" * 64,
        payload={"redacted": True},
        result=raw,
        resource_id=FileId("output"),
        attempt_count=1,
        created_at=FIXTURE_TIME,
        started_at=FIXTURE_TIME,
        finished_at=FIXTURE_TIME,
        updated_at=FIXTURE_TIME,
        expires_at=FIXTURE_TIME + timedelta(days=30),
        checkpoint_generation=0,
    )


def owned_engine(database_url: str) -> AsyncEngine:
    parsed = urlsplit(database_url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 15488
    assert parsed.path == "/wiki_round7_provider_bench"
    return create_async_engine(database_url, pool_size=1, max_overflow=0)


def sessions_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def json_sha256(value: Iterable[BaseModel]) -> str:
    raw = [model.model_dump(mode="json") for model in value]
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
