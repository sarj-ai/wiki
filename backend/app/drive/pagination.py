from __future__ import annotations

from dataclasses import dataclass
from typing import NewType, assert_never

from app.drive.errors import DriveTraversalIncompleteError


PageToken = NewType("PageToken", str)


@dataclass(frozen=True, slots=True)
class FirstPage:
    pass


@dataclass(frozen=True, slots=True)
class NextPage:
    token: PageToken
    seen: frozenset[PageToken]

    def __post_init__(self) -> None:
        if not self.token or self.token not in self.seen or not all(self.seen):
            raise DriveTraversalIncompleteError


@dataclass(frozen=True, slots=True)
class PagesComplete:
    pass


type ActivePage = FirstPage | NextPage
type Page = ActivePage | PagesComplete


def page_token(page: ActivePage) -> PageToken | None:
    match page:
        case FirstPage():
            return None
        case NextPage(token=token):
            return token
        case _:
            assert_never(page)


def page_number(page: ActivePage) -> int:
    match page:
        case FirstPage():
            return 1
        case NextPage(seen=seen):
            return len(seen) + 1
        case _:
            assert_never(page)


def advance_page(page: ActivePage, raw_token: object) -> NextPage | PagesComplete:
    if raw_token is None:
        return PagesComplete()
    if not isinstance(raw_token, str) or not raw_token:
        raise DriveTraversalIncompleteError
    match page:
        case FirstPage():
            seen = frozenset[PageToken]()
        case NextPage(seen=seen):
            pass
        case _:
            assert_never(page)
    token = PageToken(raw_token)
    if token in seen:
        raise DriveTraversalIncompleteError
    return NextPage(token, seen | {token})
