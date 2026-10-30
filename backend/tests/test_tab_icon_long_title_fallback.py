from dataclasses import replace

import pytest

from app.document_transform.tab_names import plan_tab_names
from tests.test_document_transform_tab_names import _document, _tabs


@pytest.mark.parametrize("heading", ["a" * 80, "界" * 80, "👩🏽‍💻" * 30])
@pytest.mark.parametrize("icon", [None, ""])
def test_unsafe_candidate_keeps_title_but_adds_native_icon(heading: str, icon: str | None):
    document = _document(heading)
    tabs = (replace(_tabs("Tab 1")[0], icon_emoji=icon),)
    plan = plan_tab_names(document, tabs)
    assert len(plan.renames) == 1
    rename = plan.renames[0]
    assert rename.title == "Tab 1"
    assert rename.request() == {
        "updateDocumentTabProperties": {
            "tabProperties": {"tabId": "t0", "iconEmoji": "📄"},
            "fields": "iconEmoji",
        }
    }
    result = (replace(tabs[0], icon_emoji="📄"),)
    assert plan.matches_preimage(tabs)
    assert plan.matches_result(result)
    assert not plan.matches_preimage((replace(tabs[0], icon_emoji="🚀"),))
    assert not plan.matches_result((replace(result[0], title="Concurrent"),))
    assert plan_tab_names(document, result).renames == ()


@pytest.mark.parametrize(("title", "icon"), [("Tab 1", "🚀"), ("📝 Authored", None)])
def test_long_heading_preserves_existing_icon_or_authored_prefix(title: str, icon: str | None):
    tabs = (replace(_tabs(title)[0], icon_emoji=icon),)
    assert plan_tab_names(_document("a" * 80), tabs).renames == ()


def test_long_unbreakable_security_heading_keeps_relevant_native_icon():
    plan = plan_tab_names(_document("Security:" + "a" * 80), _tabs("Tab 1"))
    assert plan.renames[0].title == "Tab 1"
    assert plan.renames[0].icon_emoji == "🔒"
