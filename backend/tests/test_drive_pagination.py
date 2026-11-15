from __future__ import annotations

import pytest

from app.drive.errors import DriveTraversalIncompleteError
from app.drive.pagination import (
    FirstPage,
    NextPage,
    PagesComplete,
    PageToken,
    advance_page,
    page_number,
    page_token,
)


def test_pages_require_provider_continuation_before_another_request() -> None:
    first = FirstPage()
    assert page_token(first) is None
    assert page_number(first) == 1
    second = advance_page(first, "one")
    assert isinstance(second, NextPage)
    assert page_token(second) == "one"
    assert page_number(second) == 2
    third = advance_page(second, "two")
    assert isinstance(third, NextPage)
    assert page_number(third) == 3
    assert advance_page(third, None) == PagesComplete()


@pytest.mark.parametrize("raw_token", ["", False, 1, [], {}, b"token"])
def test_invalid_provider_cursor_cannot_complete_a_listing(raw_token: object) -> None:
    with pytest.raises(DriveTraversalIncompleteError):
        advance_page(FirstPage(), raw_token)


@pytest.mark.parametrize("repeated", ["one", "two"])
def test_repeated_cursor_cannot_replay_an_earlier_page(repeated: str) -> None:
    second = advance_page(FirstPage(), "one")
    assert isinstance(second, NextPage)
    third = advance_page(second, "two")
    assert isinstance(third, NextPage)
    with pytest.raises(DriveTraversalIncompleteError):
        advance_page(third, repeated)


def test_continuation_requires_its_own_valid_history() -> None:
    with pytest.raises(DriveTraversalIncompleteError):
        NextPage(PageToken(""), frozenset())
    with pytest.raises(DriveTraversalIncompleteError):
        NextPage(PageToken("one"), frozenset({PageToken("two")}))
