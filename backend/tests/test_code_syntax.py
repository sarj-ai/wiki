import pytest

from app.document_transform.code_syntax import (
    MAX_CODE_CHARACTERS,
    CodeToken,
    format_code,
    is_code_font_family,
)


@pytest.mark.parametrize(
    ("hint", "expected"),
    [("py", "python"), ("PYTHON3", "python"), ("ts", "typescript"), ("js", "javascript")],
)
def test_explicit_builtin_alias_takes_precedence(hint: str, expected: str) -> None:
    result = format_code('const value = "unchanged";', hint)
    assert result.language == expected
    assert "".join(token.text for token in result.tokens) == 'const value = "unchanged";'


@pytest.mark.parametrize("hint", ["python", "ts", "json", "sql", "bash", "yaml"])
@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_lexing_preserves_original_unicode_tabs_and_line_endings(hint: str, ending: str) -> None:
    source = f'# μs café مرحبا 👩🏽‍💻{ending}\tvalue = "a  b"{ending}{ending}'
    result = format_code(source, hint)
    assert "".join(token.text for token in result.tokens) == source
    assert result.tokens


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('def greet(name):\n    return "Hello, " + name\n', "python"),
        ("interface User { name: string; }", "typescript"),
        ("const value: number = 42;", "typescript"),
        ('const value = "safe";', "javascript"),
        ('{"count": 2, "enabled": true}', "json"),
        ("SELECT name FROM users WHERE active = true;", "sql"),
        ('#!/bin/bash\nprintf "%s\\n" "$HOME"\n', "bash"),
        ("retries: 3\nenabled: true\n", "yaml"),
    ],
)
def test_unhinted_detection_requires_language_syntax(source: str, expected: str) -> None:
    result = format_code(source)
    assert result.language == expected
    assert any(token.color is not None for token in result.tokens)
    assert "".join(token.text for token in result.tokens) == source


@pytest.mark.parametrize(
    "source",
    [
        "Select the name from the list.",
        "Summary:\nThis is an ordinary paragraph.\nNext steps:\nReview the plan.",
        "Owner: Platform\nStatus: Ready\n",
        "The class starts at noon: please arrive early.",
        "{not valid JSON}",
    ],
)
def test_ambiguous_prose_remains_plain(source: str) -> None:
    result = format_code(source)
    assert result.language is None
    assert result.tokens == (CodeToken(source),)


@pytest.mark.parametrize("hint", ["text", "unknown", "../../evil.py", "python:run", "json"])
def test_explicit_hint_never_loads_files_or_guesses_another_language(hint: str) -> None:
    source = "def safe():\n    return 1\n"
    result = format_code(source, hint)
    assert "".join(token.text for token in result.tokens) == source
    if hint == "json":
        assert result.language == "json"
    else:
        assert result.language is None
        assert result.tokens == (CodeToken(source),)


def test_syntax_palette_separates_keywords_strings_and_comments() -> None:
    result = format_code('def greet():\n    # Retain this comment.\n    return "Hello"\n', "python")
    keyword = next(token for token in result.tokens if token.text == "def")
    string = next(token for token in result.tokens if '"Hello"' in token.text)
    comment = next(token for token in result.tokens if "Retain this comment." in token.text)
    assert keyword.bold
    assert keyword.color == "6D28D9"
    assert string.color == "006D77"
    assert comment.color == "62616C"


@pytest.mark.parametrize(
    "source",
    ["x" * (MAX_CODE_CHARACTERS + 1), "value = 1\n" * 200],
)
def test_large_or_token_dense_blocks_fall_back_without_losing_source(source: str) -> None:
    result = format_code(source, "python")
    assert result.language is None
    assert result.tokens == (CodeToken(source),)


def test_empty_source_has_no_invented_newline() -> None:
    result = format_code("", "python")
    assert result.language is None
    assert result.tokens == ()


@pytest.mark.parametrize(
    "font", ["Courier New", "Consolas", "Menlo", "Roboto Mono", "Fira Code", "JetBrains Mono"]
)
def test_known_monospace_families_are_identified_exactly(font: str) -> None:
    assert is_code_font_family(font)


@pytest.mark.parametrize(
    "font", ["Code2000", "Cambria Math", "Symbol", "Nunito", "Noto Sans Arabic", "Fira Code Extra"]
)
def test_symbol_script_and_similarly_named_fonts_are_not_code_fonts(font: str) -> None:
    assert not is_code_font_family(font)
