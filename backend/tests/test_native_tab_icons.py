from copy import deepcopy
from dataclasses import replace
from typing import Any

from pydantic import RootModel
import pytest

from app.document_transform.errors import DocumentGatewayError, InPlaceNeedsReviewError
from app.document_transform.ir import DocumentIR
from app.document_transform.naming import supported_tab_icon
from app.document_transform.tab_metadata import native_tab_properties
from app.document_transform.tab_names import NativeTabProperties, TabNamingPlan, plan_tab_names
from app.document_transform.tab_structure import plan_tab_structure
from app.identifiers import DocsTabId, FileId
from tests.test_document_transform_tab_metadata import _document, _plan
from tests.test_document_transform_tab_structure import _document as _split_source


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tabId", None),
        ("title", 3),
        ("index", True),
        ("index", -1),
        ("nestingLevel", False),
        ("nestingLevel", 1),
        ("parentTabId", []),
        ("parentTabId", "other"),
    ],
    ids=[
        "missing_id",
        "title_type",
        "bool_index",
        "negative_index",
        "bool_depth",
        "wrong_depth",
        "parent_type",
        "wrong_parent",
    ],
)
def test_malformed_known_tab_properties_fail_closed(field: str, value: object) -> None:
    document = _document()
    document["tabs"][0]["tabProperties"][field] = value
    with pytest.raises(InPlaceNeedsReviewError):
        native_tab_properties(document)


@pytest.mark.parametrize("icon", [None, "", "👩🏽‍💻", "🇸🇦", "⚖️"])
def test_icons_are_native_exact_durable_metadata_and_never_title_prefixes(icon: str | None) -> None:
    tab = NativeTabProperties(DocsTabId("tab"), "Security", icon_emoji=icon)
    document = DocumentIR("Title", (), "google_doc", tab_ids=(tab.tab_id,))
    plan = plan_tab_names(document, (tab,))
    payload = _TabNamingPlanPayload(plan)
    assert _TabNamingPlanPayload.model_validate_json(payload.model_dump_json()).root == plan
    expected_icon = icon or "🔒"
    after = (replace(tab, icon_emoji=expected_icon),)
    assert plan.matches_result(after)
    assert plan_tab_names(document, after).renames == ()
    assert after[0].title == "Security"
    if not icon:
        assert plan.renames[0].request() == {
            "updateDocumentTabProperties": {
                "tabProperties": {"tabId": "tab", "iconEmoji": "🔒"},
                "fields": "iconEmoji",
            }
        }
        assert not plan.matches_result((replace(after[0], icon_emoji="📄"),))
        assert not plan.matches_preimage((replace(tab, icon_emoji="📄"),))


@pytest.mark.parametrize(
    "value",
    [None, 1, False, [], "text", "😀😀", "😀\u200d", "😀\ufe0f\ufe0f"],
    ids=[
        "null",
        "integer",
        "boolean",
        "array",
        "text",
        "two_emoji",
        "dangling_joiner",
        "duplicate_variation",
    ],
)
def test_malformed_icon_is_rejected_before_native_metadata_plan(value: object) -> None:
    native = _document()
    native["tabs"][0]["tabProperties"]["iconEmoji"] = value
    with pytest.raises(InPlaceNeedsReviewError):
        native_tab_properties(native)


def test_absent_and_explicit_empty_icons_are_distinct_preimages() -> None:
    native = _document()
    absent = native_tab_properties(native)
    native["tabs"][0]["tabProperties"]["iconEmoji"] = ""
    empty = native_tab_properties(native)
    assert absent != empty
    assert absent[0].icon_emoji is None
    assert empty[0].icon_emoji is not None
    assert not empty[0].icon_emoji
    native["tabs"][0]["tabProperties"]["futurePrivateField"] = "PRIVATE"
    with pytest.raises(InPlaceNeedsReviewError):
        native_tab_properties(native)


def test_split_preserves_authored_first_icon_and_persists_new_section_icons() -> None:
    native = _split_source()
    native["tabs"][0]["tabProperties"]["iconEmoji"] = "👩🏽‍💻"
    before = deepcopy(native)
    plan = plan_tab_structure(native, comments_verified_absent=True)
    assert plan is not None
    assert plan.sections[0].icon_emoji == "👩🏽‍💻"
    assert plan.sections[2].icon_emoji == "⚠️"
    assert all(supported_tab_icon(section.icon_emoji) for section in plan.sections)
    assert native == before
    assert plan.preserves_inventory(plan.sections)
    assert not plan.preserves_inventory(
        (replace(plan.sections[0], icon_emoji="📄"), *plan.sections[1:])
    )


async def test_provider_rejects_icon_without_silent_success_or_title_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = _document()
    before = deepcopy(native)
    gateway, checkpoint = await _plan(monkeypatch, native)

    async def reject(**kwargs: Any) -> None:
        assert (
            kwargs["requests"][0]["updateDocumentTabProperties"]["tabProperties"]["iconEmoji"]
            == "📄"
        )
        raise DocumentGatewayError(http_status=400)

    monkeypatch.setattr(gateway, "apply_in_place_stage", reject)
    with pytest.raises(DocumentGatewayError) as error:
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert error.value.http_status == 400
    assert native == before


class _TabNamingPlanPayload(RootModel[TabNamingPlan]):
    pass
