from copy import deepcopy
from dataclasses import replace
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform import google_gateway as gateway_module
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import InPlaceInspection, NativeDocumentSnapshot, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway, verify_brand_application
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import TransformMode
from app.document_transform.source_access import NativeSourceAccess, PersonalDrive
from app.document_transform.verification import BrandVerification
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_copy_font_defense import _ReadbackGateway
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
    _output_draft,
)


def _branded_evidence_labels(representation: str = "explicit") -> dict[str, JsonValue]:
    document = _branded_google_document()
    document["documentId"] = "source"
    tab = document["tabs"][0]["documentTab"]
    content = tab["body"]["content"]
    plain = content[0]
    plain["paragraph"]["elements"][0]["textRun"]["content"] = "Evidence:\n"
    bold = deepcopy(plain)
    bold.update(startIndex=11, endIndex=21)
    run = bold["paragraph"]["elements"][0]
    run.update(startIndex=11, endIndex=21)
    if representation == "explicit":
        run["textRun"]["textStyle"]["bold"] = True
    elif representation == "inherited":
        tab["namedStyles"]["styles"][0]["textStyle"]["bold"] = True
        plain["paragraph"]["elements"][0]["textRun"]["textStyle"]["bold"] = False
    content.append(bold)
    return document


def test_copy_format_keeps_bold_independent_of_label_heading_level() -> None:
    document = _branded_evidence_labels()
    parsed = parse_google_document(document, title="Decision")
    requests = build_copy_mutations(
        parsed,
        parsed,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="",
        source_document=document,
    )
    body_bold = {
        style["range"]["startIndex"]: style["textStyle"]["bold"]
        for request in requests
        if (style := request.get("updateTextStyle"))
        and "segmentId" not in style["range"]
        and "bold" in style["textStyle"]
    }
    assert body_bold == {1: False, 11: True}


@pytest.mark.parametrize("representation", ["explicit", "inherited", "stripped"])
async def test_native_readback_checks_authored_label_bold(representation: str) -> None:
    source = _branded_evidence_labels()
    output = _branded_evidence_labels(representation)
    gateway = _ReadbackGateway(output)
    gateway._logo_uri = "https://wiki.sarj.ai/brand/sarj-logo-light.png"
    snapshot = _inspection_snapshot(source)
    expected = parse_google_document(source, title="Decision")
    copy = await gateway.verify_output(
        draft=_output_draft(),
        source=snapshot,
        expected=expected,
        verify_brand=True,
        header_title="Decision",
    )
    inspection = await gateway.inspect_in_place(snapshot=snapshot, title="Decision")
    in_place = await gateway.verify_in_place(
        document=output,
        title="Decision",
        expected=expected,
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=True,
    )
    for result in (copy, in_place):
        assert result.content_preserved
        assert result.structure_preserved is (representation != "stripped")
        if representation != "stripped":
            assert result.brand is BrandVerification.FULL


async def test_already_branded_format_is_zero_batch_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _InPlaceGateway("The team approved the plan.")
    inspect_original = gateway.inspect_in_place

    async def inspect(*, snapshot: SourceSnapshot, title: str) -> InPlaceInspection:
        inspection = await inspect_original(snapshot=snapshot, title=title)
        return replace(
            inspection,
            preserved_headers=inspection.headers,
            headers=(),
            brand=BrandVerification.FULL,
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
    assert inspection.brand is BrandVerification.FULL
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
    assert inspection.brand is not BrandVerification.FULL


def _inspection_snapshot(document: dict[str, JsonValue]) -> NativeDocumentSnapshot:
    return NativeDocumentSnapshot(
        title="Decision",
        parent_folder_id=FileId("folder"),
        modified_time="now",
        revision_id=DocsRevisionId("revision-2"),
        google_document=document,
        access=NativeSourceAccess(owned_by_me=True, can_edit=True, location=PersonalDrive()),
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
