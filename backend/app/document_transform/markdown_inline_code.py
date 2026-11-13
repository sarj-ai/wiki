from markdown_it import MarkdownIt
from markdown_it.rules_inline.backticks import backtick
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token


def _track_backtick(state: StateInline, *, silent: bool) -> bool:
    start = state.pos
    count = len(state.tokens)
    matched = backtick(state, silent)
    if (
        not silent
        and len(state.tokens) > count
        and state.tokens[-1].type == "code_inline"
        and "\n" in state.src[start : state.pos]
    ):
        state.tokens[-1].meta["multiline_source_code"] = True
    return matched


_MARKDOWN = MarkdownIt("commonmark").enable("table").enable("strikethrough")
_MARKDOWN.inline.ruler.at("backticks", lambda state, silent: _track_backtick(state, silent=silent))


def multiline_inline_code_lines(source: str) -> frozenset[int]:
    # The editorial IR is line-based. Preserve the containing logical block
    # when a code span crosses lines, including list/quote continuations.
    # CommonMark resolves escaped delimiters and block boundaries first.
    return frozenset(
        line
        for token in _MARKDOWN.parse(source)
        if token.type == "inline" and token.map and _contains_multiline_code(token.children or [])
        for line in range(*token.map)
    )


def _contains_multiline_code(tokens: list[Token]) -> bool:
    return any(
        token.meta.get("multiline_source_code") is True
        or _contains_multiline_code(token.children or [])
        for token in tokens
    )
