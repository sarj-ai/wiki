from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform import google_gateway as gateway_module
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway, verify_brand_application
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import TransformMode
from app.drive.schemas import MIME_DOC
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
)


async def test_already_branded_format_is_zero_batch_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _InPlaceGateway("The team approved the plan.")
    inspect_original = gateway.inspect_in_place

    async def inspect(*, snapshot: SourceSnapshot, title: str):
        inspection = await inspect_original(snapshot=snapshot, title=title)
        return replace(
            inspection, preserved_headers=inspection.headers, headers=(), fully_branded=True
        )

    monkeypatch.setattr(gateway, "inspect_in_place", inspect)
    store = _CheckpointStore(gateway.events)
    result = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    ).execute(operation_id=uuid.uuid7(), request=_in_place_request(TransformMode.FORMAT))
    assert "apply" not in gateway.events
    assert result.changed is False
    assert result.source_revision_id == result.output_revision_id


def test_existing_brand_header_is_preserved_without_reinsertion() -> None:
    document = _branded_google_document()
    document["documentId"] = "source"
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://wiki.sarj.ai/brand/sarj-logo-light.png"
    snapshot = _inspection_snapshot(document)
    inspection = gateway._inspect_in_place_sync(snapshot, "Decision")
    assert inspection.headers == ()
    assert len(inspection.preserved_headers) == 1
    assert inspection.unknown_header_tab_ids == ()
    assert inspection.fully_branded
    parsed = parse_google_document(document, title="Decision")
    mutations = build_copy_mutations(
        parsed,
        parsed,
        profile=SarjBrandProfile(),
        headers=inspection.headers,
        logo_uri=inspection.logo_uri,
    )
    assert not any("insertInlineImage" in item or "insertText" in item for item in mutations)


@pytest.mark.parametrize(
    "conflict", ["title", "asset", "extra_header"], ids=["title", "asset", "extra-header"]
)
def test_existing_header_conflicts_stay_blocked(conflict: str) -> None:
    document = _branded_google_document()
    document["documentId"] = "source"
    tab = document["tabs"][0]["documentTab"]
    match conflict:
        case "title":
            tab["headers"]["header-a"]["content"][0]["paragraph"]["elements"][0]["textRun"][
                "content"
            ] = "Foreign\t"
        case "asset":
            tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"][
                "imageProperties"
            ]["sourceUri"] = "https://example.com/foreign.png"
        case _:
            tab["headers"]["other"] = deepcopy(tab["headers"]["header-a"])
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://wiki.sarj.ai/brand/sarj-logo-light.png"
    snapshot = _inspection_snapshot(document)
    inspection = gateway._inspect_in_place_sync(snapshot, "Decision")
    assert inspection.unknown_header_tab_ids == (("tab-a",) if conflict == "extra_header" else ())
    assert bool(inspection.custom_furniture) is (conflict != "extra_header")
    assert not inspection.preserved_headers
    assert not inspection.fully_branded


def _inspection_snapshot(document: dict[str, Any]) -> SourceSnapshot:
    return SourceSnapshot(
        title="Decision",
        parent_folder_id=FileId("folder"),
        modified_time="now",
        revision_id=DocsRevisionId("revision-2"),
        media_type=MIME_DOC,
        google_document=document,
        owned_by_me=True,
        can_edit=True,
        is_personal_drive=True,
    )


@pytest.mark.parametrize("local_font", [None, "Arial"], ids=["inherited", "local-override"])
def test_brand_verification_resolves_actual_named_style_inheritance(local_font: str | None) -> None:
    document = _branded_google_document()
    tab = document["tabs"][0]["documentTab"]
    paragraph = tab["body"]["content"][0]["paragraph"]
    inherited_paragraph = paragraph.pop("paragraphStyle")
    inherited_text = paragraph["elements"][0]["textRun"].pop("textStyle")
    tab["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "paragraphStyle": inherited_paragraph,
                "textStyle": inherited_text,
            }
        ]
    }
    paragraph["paragraphStyle"] = {"namedStyleType": "NORMAL_TEXT"}
    if local_font:
        paragraph["elements"][0]["textRun"]["textStyle"] = {
            "weightedFontFamily": {"fontFamily": local_font}
        }
    assert verify_brand_application(
        document, parse_google_document(document, title="Decision")
    ) is (local_font is None)


def test_unresolved_font_does_not_assume_brand_default() -> None:
    document = _branded_google_document()
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["elements"][0]["textRun"]["textStyle"].pop("weightedFontFamily")
    assert not verify_brand_application(document, parse_google_document(document, title="Decision"))


def test_provider_omitted_zero_components_are_distinct_from_absent_style() -> None:
    assert gateway_module._dimension_matches({"unit": "PT"}, 0)
    assert not gateway_module._dimension_matches(None, 0)
    assert gateway_module._color_matches({"color": {"rgbColor": {}}}, "#000000")
    assert not gateway_module._color_matches(None, "#000000")


def test_missing_boolean_requires_actual_inherited_value() -> None:
    document = _branded_google_document()
    tab = document["tabs"][0]["documentTab"]
    paragraph = tab["body"]["content"][0]["paragraph"]
    paragraph["paragraphStyle"].pop("keepWithNext")
    parsed = parse_google_document(document, title="Decision")
    assert not verify_brand_application(document, parsed)
    tab["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "paragraphStyle": {"keepWithNext": False},
            }
        ]
    }
    assert verify_brand_application(document, parsed)
    tab["namedStyles"]["styles"][0]["paragraphStyle"]["keepWithNext"] = True
    assert not verify_brand_application(document, parsed)
