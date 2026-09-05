from dataclasses import replace

import pytest

from app.document_transform.ir import DocumentIR, utf16_length
from app.document_transform.naming import tab_icon_emoji, title_case_document_name
from app.document_transform.tab_names import NativeTabProperties, plan_tab_names, truncate_tab_title
from app.document_transform.tab_structure import plan_tab_structure
from app.identifiers import DocsTabId
from tests.test_document_transform_tab_names import _document, _tabs
from tests.test_document_transform_tab_structure import _document as _native_document


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("api design and github setup", "API Design and GitHub Setup"),
        ("a guide to the mcp", "A Guide to the MCP"),
        ("iPhone and FastAPI QA", "iPhone and FastAPI QA"),
        ("خطة الفريق 2026-09-05", "خطة الفريق 2026-09-05"),
        ("École café and naïve", "École café and naïve"),
        ("NASA HTTP v2.1", "NASA HTTP v2.1"),
        ("api design: a guide to security", "API Design: A Guide to Security"),
    ],
)
def test_title_case_is_idempotent_and_preserves_identifiers_and_dates(
    source: str, expected: str
) -> None:
    assert title_case_document_name(source) == expected
    assert title_case_document_name(expected) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Goals", "🎯 Goals"),
        ("القيم", "💎 القيم"),
        ("Architecture", "⚙️ Architecture"),
        ("Decisions", "⚖️ Decisions"),
        ("👩🏽‍💻 Engineering", "👩🏽‍💻 Engineering"),
        ("🇸🇦 Team", "🇸🇦 Team"),
        ("1️⃣ Tasks", "1️⃣ Tasks"),
        ("Other", "📄 Other"),
        ("Notes", "📝 Notes"),
        ("Context", "📋 Context"),
        ("Overview", "📋 Overview"),
        ("Security", "🔒 Security"),
        ("Noteworthy insecure contextual", "📄 Noteworthy insecure contextual"),
    ],
)
def test_tab_emoji_is_relevant_and_never_duplicated(source: str, expected: str) -> None:
    assert tab_icon_emoji(source) == (
        None if source == expected else expected.split(" ", maxsplit=1)[0]
    )
    assert tab_icon_emoji(expected) is None


@pytest.mark.parametrize("token", ["👩🏽‍💻", "🇸🇦", "e\u0301", "क्ष", "x" * 61])
def test_truncation_never_splits_a_token_or_grapheme(token: str) -> None:
    assert not truncate_tab_title(token, utf16_length(token) - 1)
    assert truncate_tab_title(f"Goals {token}", 5) == "Goals"


@pytest.mark.parametrize("title", ["Company values", "👩🏽‍💻 Engineering", "x" * 60])
def test_existing_tab_text_preserved_and_replay_noop(title: str) -> None:
    tab_id = DocsTabId("tab")
    document = DocumentIR("Name", (), "google_doc", tab_ids=(tab_id,))
    tabs = (NativeTabProperties(tab_id, title),)
    plan = plan_tab_names(document, tabs)
    expected = title
    observed = tuple(
        replace(
            tab,
            title=next(
                (item.title for item in plan.renames if item.tab_id == tab.tab_id), tab.title
            ),
            icon_emoji=next(
                (item.icon_emoji for item in plan.renames if item.tab_id == tab.tab_id),
                tab.icon_emoji,
            ),
        )
        for tab in tabs
    )
    assert observed[0].title == expected
    assert plan.matches_result(observed)
    assert plan_tab_names(document, observed).renames == ()


def test_generic_names_and_sibling_collisions_are_deterministic() -> None:
    document = _document("goals and actions", "goals and actions")
    tabs = _tabs("Tab 1", "Tab 2")
    plan = plan_tab_names(document, tabs)
    assert [rename.title for rename in plan.renames] == [
        "Goals and Actions",
        "Goals and Actions (2)",
    ]
    assert all(utf16_length(rename.title) <= 60 for rename in plan.renames)
    assert (
        plan_tab_names(document, _tabs("Goals", "🎯 Goals"))
        .renames[0]
        .request()["updateDocumentTabProperties"]["fields"]
        == "iconEmoji"
    )


def test_split_names_share_emoji_policy_without_changing_source_content() -> None:
    native = _native_document()
    plan = plan_tab_structure(native, comments_verified_absent=True)
    assert plan is not None
    assert [section.title for section in plan.sections] == [
        "Priorities",
        "Delivery",
        "Risks",
    ]
    assert plan.preserves_inventory(plan.sections)
    assert [section.icon_emoji for section in plan.sections] == ["📄", "📄", "⚠️"]
    assert plan == plan_tab_structure(native, comments_verified_absent=True)
