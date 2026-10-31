from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any, Literal, NamedTuple, override
import uuid

from pydantic import JsonValue, ValidationError
import pytest

from app.document_transform.errors import InPlaceNeedsReviewError, UnsupportedTabMetadataError
from app.document_transform.gateway import InPlaceStageResult
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.schemas import TabNamingCheckpoint
from app.document_transform.tab_metadata import (
    native_tab_properties,
    tab_naming_invariant_sha256,
    versioned_tab_naming_invariant_sha256,
)
from app.identifiers import DocsRevisionId, FileId


class _Stage:
    def __init__(self, result: InPlaceStageResult | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def apply(self, **kwargs: object) -> InPlaceStageResult:
        self.calls.append(kwargs)
        assert self.result is not None
        return self.result


def _document() -> dict[str, Any]:
    return {
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t1", "title": "Tab 1"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 9,
                                "paragraph": {
                                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 9,
                                            "textRun": {"content": "Roadmap\n"},
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                    "headers": {"h1": {"content": [{"opaque": "preserve"}]}},
                    "footers": {"f1": {"content": [{"opaque": "preserve"}]}},
                },
            }
        ],
    }


class _Planned(NamedTuple):
    gateway: GoogleDocumentGateway
    checkpoint: TabNamingCheckpoint


async def _plan(monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]) -> _Planned:
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: document)
    checkpoint = await gateway.plan_tab_naming(
        document_id=FileId("doc"),
        title="Plan",
        operation_id=uuid.uuid7(),
        required_revision_id=DocsRevisionId("r1"),
    )
    assert checkpoint is not None
    return _Planned(gateway, TabNamingCheckpoint.model_validate_json(checkpoint.model_dump_json()))


def _apply(document: dict[str, Any], checkpoint: TabNamingCheckpoint) -> None:
    document["tabs"][0]["tabProperties"]["title"] = "Roadmap"
    document["tabs"][0]["tabProperties"]["iconEmoji"] = "📄"
    document["tabs"][0]["documentTab"]["namedRanges"] = {
        checkpoint.marker_name: {
            "namedRanges": [
                {
                    "name": checkpoint.marker_name,
                    "ranges": [checkpoint.marker_range],
                }
            ]
        },
    }
    document["revisionId"] = "r2"


@pytest.mark.asyncio
async def test_native_tab_stage_commits_once_and_recovers_lost_response(
    monkeypatch: pytest.MonkeyPatch,
):
    document = _document()
    gateway, checkpoint = await _plan(monkeypatch, document)
    after = deepcopy(document)
    _apply(after, checkpoint)
    stage = _Stage(InPlaceStageResult(DocsRevisionId("r2"), after))
    monkeypatch.setattr(gateway, "apply_in_place_stage", stage.apply)
    assert await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint) == "r2"
    assert stage.calls[0]["required_revision_id"] == "r1"
    assert stage.calls[0]["requests"] == [
        {
            "updateDocumentTabProperties": {
                "tabProperties": {"tabId": "t1", "title": "Roadmap", "iconEmoji": "📄"},
                "fields": "title,iconEmoji",
            },
        }
    ]
    _apply(document, checkpoint)
    assert await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint) == "r2"
    assert len(stage.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["revision", "title", "footer", "body", "header"])
async def test_tab_naming_rejects_concurrent_edits(monkeypatch: pytest.MonkeyPatch, conflict: str):
    document = _document()
    gateway, checkpoint = await _plan(monkeypatch, document)
    tab = document["tabs"][0]
    match conflict:
        case "revision":
            document["revisionId"] = "changed"
        case "title":
            tab["tabProperties"]["title"] = "User authored"
        case _:
            tab["documentTab"][
                {"footer": "footers", "header": "headers", "body": "body"}[conflict]
            ] = {"changed": True}
    stage = _Stage()
    monkeypatch.setattr(gateway, "apply_in_place_stage", stage.apply)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert not stage.calls


@pytest.mark.asyncio
async def test_tab_naming_rejects_wrong_post_write_title(monkeypatch: pytest.MonkeyPatch):
    document = _document()
    gateway, checkpoint = await _plan(monkeypatch, document)
    stage = _Stage(InPlaceStageResult(DocsRevisionId("r2"), document))
    monkeypatch.setattr(gateway, "apply_in_place_stage", stage.apply)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)


def test_tab_metadata_nested_topology_and_full_content_invariant():
    document = _document()
    original_hash = tab_naming_invariant_sha256(document)
    document["tabs"][0]["tabProperties"]["title"] = "Roadmap"
    assert tab_naming_invariant_sha256(document) == original_hash
    child = deepcopy(document["tabs"][0])
    child["tabProperties"] = {"tabId": "child", "title": "Details"}
    document["tabs"][0]["childTabs"] = [child]
    assert native_tab_properties(document)[1].parent_tab_id == "t1"
    assert tab_naming_invariant_sha256(document) != original_hash


def test_unknown_tab_metadata_is_a_typed_optional_enhancement_decline() -> None:
    document = _document()
    document["tabs"][0]["tabProperties"]["futureProviderField"] = "preserve"

    with pytest.raises(UnsupportedTabMetadataError):
        native_tab_properties(document)


class _IllustratedDocument(NamedTuple):
    document: dict[str, JsonValue]
    image_properties: dict[str, JsonValue]
    embedded_object: dict[str, JsonValue]


class _ImageTabGateway(GoogleDocumentGateway):
    def __init__(self, document: dict[str, JsonValue]) -> None:
        self.document = document
        self.stage = _Stage()

    @override
    def _get_document_sync(self, file_id: FileId) -> dict[str, JsonValue]:
        assert file_id == "doc"
        return self.document

    @override
    async def apply_in_place_stage(
        self,
        *,
        document_id: FileId,
        required_revision_id: DocsRevisionId,
        marker_name: str,
        marker_range: Mapping[str, JsonValue],
        requests: Sequence[Mapping[str, JsonValue]],
    ) -> InPlaceStageResult:
        return await self.stage.apply(
            document_id=document_id,
            required_revision_id=required_revision_id,
            marker_name=marker_name,
            marker_range=marker_range,
            requests=requests,
        )

    async def planned_checkpoint(self) -> TabNamingCheckpoint:
        checkpoint = await self.plan_tab_naming(
            document_id=FileId("doc"),
            title="Plan",
            operation_id=uuid.uuid7(),
            required_revision_id=DocsRevisionId("r1"),
        )
        assert checkpoint is not None
        return TabNamingCheckpoint.model_validate_json(checkpoint.model_dump_json())


@pytest.fixture
def illustrated_document_factory() -> Callable[[str], _IllustratedDocument]:
    def create(content_uri: str) -> _IllustratedDocument:
        image_properties: dict[str, JsonValue] = {
            "contentUri": content_uri,
            "sourceUri": "https://example.com/original-photo.jpg",
            "cropProperties": {"offsetTop": 0},
        }
        embedded_object: dict[str, JsonValue] = {
            "imageProperties": image_properties,
            "size": {"width": {"magnitude": 100, "unit": "PT"}},
        }
        document = _document()
        document["tabs"][0]["documentTab"]["inlineObjects"] = {
            "image-1": {"inlineObjectProperties": {"embeddedObject": embedded_object}}
        }
        document["providerMetadata"] = {"contentUri": "https://example.com/meaningful-uri"}
        return _IllustratedDocument(document, image_properties, embedded_object)

    return create


async def test_tab_image_urls_can_rotate_before_after_and_during_recovery(
    illustrated_document_factory: Callable[[str], _IllustratedDocument],
) -> None:
    source = illustrated_document_factory("https://example.com/temporary-v1")
    gateway = _ImageTabGateway(source.document)
    checkpoint = await gateway.planned_checkpoint()
    assert checkpoint.invariant_version == "stable-images-v2"
    source.image_properties["contentUri"] = "https://example.com/temporary-v2"
    after = illustrated_document_factory("https://example.com/temporary-v3")
    _apply(after.document, checkpoint)
    gateway.stage.result = InPlaceStageResult(DocsRevisionId("r2"), after.document)
    assert await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint) == "r2"
    _apply(source.document, checkpoint)
    source.image_properties["contentUri"] = "https://example.com/temporary-v4"
    assert await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint) == "r2"
    assert len(gateway.stage.calls) == 1


async def test_legacy_tab_checkpoint_keeps_its_original_raw_image_hash(
    illustrated_document_factory: Callable[[str], _IllustratedDocument],
) -> None:
    source = illustrated_document_factory("https://example.com/temporary-v1")
    gateway = _ImageTabGateway(source.document)
    checkpoint = await gateway.planned_checkpoint()
    payload = checkpoint.model_dump(mode="json")
    payload.pop("invariant_version")
    payload["invariant_sha256"] = tab_naming_invariant_sha256(source.document)
    legacy = TabNamingCheckpoint.model_validate(payload)
    assert legacy.invariant_version is None
    source.image_properties["contentUri"] = "https://example.com/temporary-v2"
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=legacy)
    assert not gateway.stage.calls


@pytest.mark.parametrize(
    ("changed_field", "value"),
    [
        ("sourceUri", "https://example.com/replaced-photo.jpg"),
        ("cropProperties", {"offsetTop": 0.25}),
        ("size", {"width": {"magnitude": 50, "unit": "PT"}}),
        ("providerMetadata", {"contentUri": "https://example.com/different-meaningful-uri"}),
    ],
)
async def test_tab_image_normalization_keeps_source_crop_size_and_unrelated_uris_protected(
    illustrated_document_factory: Callable[[str], _IllustratedDocument],
    changed_field: str,
    value: JsonValue,
) -> None:
    source = illustrated_document_factory("https://example.com/temporary-v1")
    gateway = _ImageTabGateway(source.document)
    checkpoint = await gateway.planned_checkpoint()
    match changed_field:
        case "size":
            source.embedded_object[changed_field] = value
        case "providerMetadata":
            source.document[changed_field] = value
        case _:
            source.image_properties[changed_field] = value
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert not gateway.stage.calls


async def test_tab_image_replacement_after_write_still_fails_verification(
    illustrated_document_factory: Callable[[str], _IllustratedDocument],
) -> None:
    source = illustrated_document_factory("https://example.com/temporary-v1")
    gateway = _ImageTabGateway(source.document)
    checkpoint = await gateway.planned_checkpoint()
    after = illustrated_document_factory("https://example.com/temporary-v2")
    after.image_properties["sourceUri"] = "https://example.com/replaced-photo.jpg"
    _apply(after.document, checkpoint)
    gateway.stage.result = InPlaceStageResult(DocsRevisionId("r2"), after.document)
    with pytest.raises(InPlaceNeedsReviewError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert len(gateway.stage.calls) == 1


async def test_unknown_tab_invariant_version_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, checkpoint = await _plan(monkeypatch, _document())
    payload = checkpoint.model_dump(mode="json")
    payload["invariant_version"] = "future-unknown-version"
    with pytest.raises(ValidationError) as caught:
        TabNamingCheckpoint.model_validate(payload)
    assert any(
        error["loc"] == ("invariant_version",) and error["type"] == "literal_error"
        for error in caught.value.errors()
    )


@pytest.mark.parametrize("version", [None, "stable-images-v2"])
def test_tab_invariant_requires_a_document_object(
    version: Literal["stable-images-v2"] | None,
) -> None:
    with pytest.raises(TypeError, match="document object"):
        versioned_tab_naming_invariant_sha256([], version=version)
