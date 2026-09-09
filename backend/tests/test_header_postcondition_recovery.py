from copy import deepcopy
from dataclasses import replace
from typing import Any, override
import uuid

import pytest

from app.document_transform import google_gateway, header_recovery
from app.document_transform.errors import (
    DocumentGatewayError,
    InPlaceNeedsReviewError,
    SourceChangedError,
)
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import (
    InPlaceInspection,
    InPlaceStageResult,
    OutputVerification,
    SourceSnapshot,
)
from app.document_transform.header_recovery import recover_empty_header_postcondition
from app.document_transform.native_table_font import TableFontPlan
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    InPlaceCheckpoint,
    OutputStrategy,
    TransformMode,
)
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_custom_furniture import (
    _apply_header_title_requests,
    _custom_document,
    _native_gateway,
)
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _Gateway,
    _Generator,
)
from tests.test_document_transform_repeat import _inspection_snapshot


class _HeaderGateway(_Gateway):
    def __init__(self, *, newline: bool = False, custom: bool = False) -> None:
        document = _branded_google_document()
        document["documentId"] = "source"
        original_tab = document["tabs"][0]
        document["tabs"] = []
        for ordinal in range(6):
            tab = deepcopy(original_tab)
            tab["tabProperties"]["tabId"] = f"tab-{ordinal}"
            tab["documentTab"]["headers"] = {}
            del tab["documentTab"]["documentStyle"]["defaultHeaderId"]
            document["tabs"].append(tab)
        if custom:
            tab = _custom_document(footer=True)["tabs"][0]
            tab["tabProperties"]["tabId"] = "custom-tab"
            document["tabs"].append(tab)
        super().__init__(_inspection_snapshot(document))
        self.document = document
        self.native = _native_gateway()
        self.newline = newline
        self.mutations: list[str] = []
        self.race = False

    @override
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot:
        del source
        return replace(
            _inspection_snapshot(deepcopy(self.document)),
            revision_id=DocsRevisionId(self.document["revisionId"]),
        )

    @override
    async def inspect_in_place(
        self, *, snapshot: SourceSnapshot, title: str, table_font_plan: TableFontPlan | None = None
    ) -> InPlaceInspection:
        return await self.native.inspect_in_place(
            snapshot=snapshot, title=title, table_font_plan=table_font_plan
        )

    @override
    async def verify_in_place(self, **kwargs: Any) -> OutputVerification:
        return await self.native.verify_in_place(**kwargs)

    @override
    async def apply_in_place_stage(self, **kwargs: Any) -> InPlaceStageResult:
        is_header = any("createHeader" in request for request in kwargs["requests"])
        if self.race and not is_header:
            self.document["revisionId"] = "concurrent"
        if kwargs["required_revision_id"] != self.document["revisionId"]:
            raise SourceChangedError
        self.mutations.append("headers" if is_header else "content")
        if is_header:
            for ordinal, tab in enumerate(self.document["tabs"]):
                if tab["tabProperties"]["tabId"] == "custom-tab":
                    continue
                identifier = f"created-{ordinal}"
                tab["documentTab"]["documentStyle"]["defaultHeaderId"] = identifier
                content = (
                    [
                        {
                            "endIndex": 1,
                            "paragraph": {
                                "elements": [{"endIndex": 1, "textRun": {"content": "\n"}}],
                                "paragraphStyle": {
                                    "namedStyleType": "NORMAL_TEXT",
                                    "direction": "LEFT_TO_RIGHT",
                                },
                            },
                        }
                    ]
                    if self.newline
                    else []
                )
                tab["documentTab"]["headers"] = {
                    identifier: {"headerId": identifier, "content": content}
                }
            marker = kwargs["marker_name"]
            self.document["tabs"][-1]["documentTab"]["namedRanges"] = {
                marker: {
                    "namedRanges": [
                        {
                            "name": marker,
                            "ranges": [
                                deepcopy(kwargs["marker_range"]),
                                {
                                    "startIndex": 1,
                                    "endIndex": 2,
                                    "tabId": kwargs["marker_range"]["tabId"],
                                },
                            ],
                        }
                    ]
                }
            }
            self.document["revisionId"] = "headers-created"
            raise DocumentGatewayError
        for tab in self.document["tabs"]:
            identifier = tab["documentTab"]["documentStyle"]["defaultHeaderId"]
            if not identifier.startswith("created-"):
                continue
            tab["documentTab"]["headers"][identifier] = deepcopy(
                _branded_google_document()["tabs"][0]["documentTab"]["headers"]["header-a"]
            )
        custom_requests = tuple(
            request
            for request in kwargs["requests"]
            if (
                request.get("insertText", {}).get("location", {}).get("segmentId") == "header-a"
                or request.get("updateTextStyle", {}).get("range", {}).get("segmentId")
                == "header-a"
            )
        )
        if custom_requests:
            _apply_header_title_requests(self.document, custom_requests)
        self.document["revisionId"] = "content-applied"
        return InPlaceStageResult(DocsRevisionId("content-applied"), deepcopy(self.document))


class _Session:
    def __init__(self, *, newline: bool = False, custom: bool = False) -> None:
        self.gateway = _HeaderGateway(newline=newline, custom=custom)
        self.store = _CheckpointStore([])
        self.operation = uuid.uuid7()
        self.task_id = TaskId(uuid.uuid7())
        self.request = DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
            ),
            mode=TransformMode.FORMAT,
            output_strategy=OutputStrategy.IN_PLACE,
        )

    async def run(self, checkpoint: InPlaceCheckpoint | None = None):
        return await DocumentTransformExecutor(
            gateway=self.gateway,
            generator=_Generator(),
            checkpoint_store=self.store,
            task_id=self.task_id,
            lease_token=uuid.uuid7(),
            checkpoint=checkpoint,
            checkpoint_generation=self.store.generation,
        ).execute(operation_id=self.operation, request=self.request)


@pytest.mark.parametrize("newline", [False, True])
@pytest.mark.parametrize("custom", [False, True])
async def test_six_header_two_range_marker_recovers_once_without_relaxing_marker(
    *, newline: bool, custom: bool
) -> None:
    session = _Session(newline=newline, custom=custom)
    with pytest.raises(DocumentGatewayError):
        await session.run()
    checkpoint = session.store.checkpoints[-1]
    assert checkpoint.marker_name is not None
    assert checkpoint.marker_range is not None
    assert not google_gateway._has_exact_named_range(
        session.gateway.document, checkpoint.marker_name, checkpoint.marker_range
    )
    checks = google_gateway._named_range_diagnostics(
        session.gateway.document, checkpoint.marker_name, checkpoint.marker_range
    )
    assert (checks["definition_count"], checks["range_count"], checks["exact_range_count"]) == (
        1,
        2,
        1,
    )
    result = await session.run(checkpoint)
    assert result.changed
    assert not result.needs_review
    assert all(finding.severity.value == "informational" for finding in result.findings)
    assert session.gateway.mutations == ["headers", "content"]
    assert await session.run(session.store.checkpoints[-1]) == result
    assert session.gateway.mutations == ["headers", "content"]


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "extra",
        "populated",
        "first_page",
        "body",
        "old_headers",
        "old_preserved",
        "digest",
        "marker",
        "geometry",
    ],
)
async def test_header_postcondition_rejects_unproven_state(change: str) -> None:
    session = _Session()
    with pytest.raises(DocumentGatewayError):
        await session.run()
    checkpoint = session.store.checkpoints[-1]
    tab = session.gateway.document["tabs"][0]["documentTab"]
    match change:
        case "missing":
            tab["headers"] = {}
            del tab["documentStyle"]["defaultHeaderId"]
        case "extra":
            tab["headers"]["unreferenced"] = {"content": []}
        case "populated":
            tab["headers"]["created-0"]["content"] = [
                {"paragraph": {"elements": [{"textRun": {"content": "Authored\n"}}]}}
            ]
        case "first_page":
            tab["documentStyle"]["firstPageHeaderId"] = "created-0"
        case "body":
            tab["body"]["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] = (
                "Changed.\n"
            )
        case "old_headers":
            checkpoint = checkpoint.model_copy(update={"headers": {"tab-0": "old"}})
        case "old_preserved":
            checkpoint = checkpoint.model_copy(update={"preserved_headers": {"tab-0": "old"}})
        case "digest":
            checkpoint = checkpoint.model_copy(update={"mutation_sha256": "wrong"})
        case "marker":
            checkpoint = checkpoint.model_copy(update={"marker_name": "wrong"})
        case "geometry":
            tab["documentStyle"]["marginTop"] = {"magnitude": 120, "unit": "PT"}
        case _:
            raise AssertionError
    assert isinstance(session.request.source, DriveFileSource)
    snapshot = await session.gateway.load_drive_source(session.request.source)
    inspection = await session.gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
    assert (
        recover_empty_header_postcondition(snapshot, inspection, checkpoint, session.operation)
        is None
    )
    assert session.gateway.mutations == ["headers"]


async def test_recovered_headers_do_not_rebase_concurrent_content_revision() -> None:
    session = _Session()
    with pytest.raises(DocumentGatewayError):
        await session.run()
    session.gateway.race = True
    with pytest.raises((SourceChangedError, InPlaceNeedsReviewError)):
        await session.run(session.store.checkpoints[-1])
    assert session.gateway.mutations == ["headers"]


@pytest.mark.parametrize(
    "change",
    [
        "paragraph_unknown",
        "run_unknown",
        "link",
        "suggestion",
        "style",
        "bold",
        "boolean",
        "negative",
        "start",
        "end",
        "direction_rtl",
        "direction_unknown",
        "direction_null",
        "style_extra",
        "content",
    ],
)
def test_empty_header_rejects_noncanonical_payload(change: str) -> None:
    run: dict[str, Any] = {"startIndex": 0, "endIndex": 1, "textRun": {"content": "\n"}}
    paragraph: dict[str, Any] = {
        "elements": [run],
        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT", "direction": "LEFT_TO_RIGHT"},
    }
    element: dict[str, Any] = {"startIndex": 0, "endIndex": 1, "paragraph": paragraph}
    header = {"headerId": "created", "content": [element]}
    assert header_recovery._empty_header(header, "created")
    match change:
        case "paragraph_unknown":
            paragraph["unknown"] = True
        case "run_unknown":
            run["unknown"] = True
        case "link":
            run["textRun"]["textStyle"] = {"link": {"url": "https://example.test"}}
        case "suggestion":
            run["suggestedInsertionIds"] = ["suggestion"]
        case "style":
            paragraph["paragraphStyle"] = {"namedStyleType": "HEADING_1"}
        case "bold":
            run["textRun"]["textStyle"] = {"bold": True}
        case "boolean":
            run["startIndex"] = False
        case "negative":
            element["startIndex"] = -1
        case "start":
            run["startIndex"] = 1
        case "end":
            element["endIndex"] = 2
        case "direction_rtl":
            paragraph["paragraphStyle"]["direction"] = "RIGHT_TO_LEFT"
        case "direction_unknown":
            paragraph["paragraphStyle"]["direction"] = "UNKNOWN"
        case "direction_null":
            paragraph["paragraphStyle"]["direction"] = None
        case "style_extra":
            paragraph["paragraphStyle"]["alignment"] = "START"
        case "content":
            run["textRun"]["content"] = "x"
        case _:
            raise AssertionError
    assert not header_recovery._empty_header(header, "created")


@pytest.mark.parametrize(
    "style",
    [
        {},
        {"namedStyleType": "NORMAL_TEXT"},
        {"direction": "LEFT_TO_RIGHT"},
        {"namedStyleType": "NORMAL_TEXT", "direction": "LEFT_TO_RIGHT"},
    ],
    ids=["empty", "normal-text", "left-to-right", "normal-text-left-to-right"],
)
def test_empty_header_accepts_only_optional_observed_defaults(style: dict[str, str]) -> None:
    header = {
        "headerId": "created",
        "content": [
            {
                "endIndex": 1,
                "paragraph": {
                    "paragraphStyle": style,
                    "elements": [{"endIndex": 1, "textRun": {"content": "\n"}}],
                },
            }
        ],
    }
    checks: dict[str, bool | str] = {}
    assert header_recovery._empty_header(header, "created", checks)
    assert checks["header_recovery_empty_reason"] == "empty"


async def test_existing_custom_header_tampering_prevents_recovery() -> None:
    session = _Session(custom=True)
    with pytest.raises(DocumentGatewayError):
        await session.run()
    tab = session.gateway.document["tabs"][-1]["documentTab"]
    header = tab["headers"][tab["documentStyle"]["defaultHeaderId"]]
    header["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] = "Changed\n"
    snapshot = await session.gateway.load_drive_source(
        DriveFileSource(kind="drive_file", file_id=FileId("source"), expected_modified_time="now")
    )
    inspection = await session.gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
    assert (
        recover_empty_header_postcondition(
            snapshot, inspection, session.store.checkpoints[-1], session.operation
        )
        is None
    )
    assert session.gateway.mutations == ["headers"]
