from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Final

from pygments.lexer import Lexer
from pygments.lexers.data import JsonLexer, YamlLexer
from pygments.lexers.javascript import JavascriptLexer, TypeScriptLexer
from pygments.lexers.python import PythonLexer
from pygments.lexers.shell import BashLexer
from pygments.lexers.sql import SqlLexer


MAX_CODE_CHARACTERS = 32_768
MAX_CODE_TOKENS = 512
_MAX_LANGUAGE_HINT_LENGTH = 64
_MIN_YAML_ENTRIES = 2
_LEXERS: Final[dict[str, type[Lexer]]] = {
    "python": PythonLexer,
    "typescript": TypeScriptLexer,
    "javascript": JavascriptLexer,
    "json": JsonLexer,
    "sql": SqlLexer,
    "bash": BashLexer,
    "yaml": YamlLexer,
}
_ALIASES: Final[dict[str, str]] = {
    "py": "python",
    "python3": "python",
    "ts": "typescript",
    "js": "javascript",
    "node": "javascript",
    "sh": "bash",
    "shell": "bash",
    "zsh": "bash",
    "yml": "yaml",
}
_CODE_FONTS = frozenset(
    {
        "andalemono",
        "cascadiacode",
        "cascadiamono",
        "consolas",
        "courier",
        "couriernew",
        "dejavusansmono",
        "droidsansmono",
        "firacode",
        "ibmplexmono",
        "inconsolata",
        "jetbrainsmono",
        "liberationmono",
        "lucidaconsole",
        "menlo",
        "monaco",
        "monospace",
        "notosansmono",
        "robotomono",
        "sfmono",
        "sourcecodepro",
        "ubuntumono",
    }
)
_SIGNATURES = (
    ("python", r"(?m)^(?:async +)?def +\w+\([^\n]*\).*:\s*$|^class +\w+[^\n]*:\s*$"),
    ("python", r"(?m)^(?:from +[\w.]+ +import +\w|import +[\w.]+\s*$)"),
    ("typescript", r"(?m)^(?:export +)?(?:interface +\w+\s*\{|type +\w+\s*=)"),
    ("typescript", r"(?m)^(?:export +)?(?:const|let) +\w+\s*:\s*[\w\[\]|<>]+\s*="),
    ("javascript", r"(?m)^(?:export +)?(?:const|let|var) +\w+\s*=|^(?:async +)?function +\w+\("),
    ("javascript", r"(?m)^console\.(?:log|warn|error)\("),
    ("bash", r"\A#![^\n]*\b(?:bash|sh|zsh)\b|(?m:^\s*(?:export +\w+=|\w+=\$\())"),
    ("sql", r"(?s)\A\s*(?:SELECT\b.+\bFROM\b|CREATE +TABLE\b|INSERT +INTO\b|UPDATE\b.+\bSET\b)"),
)
_YAML_ENTRY = re.compile(r"[\w.-]+:\s+\S.*")


@dataclass(frozen=True, slots=True)
class CodeToken:
    text: str
    color: str | None = None
    bold: bool = False


@dataclass(frozen=True, slots=True)
class CodeFormatting:
    language: str | None
    tokens: tuple[CodeToken, ...]


def is_code_font_family(font: str) -> bool:
    return re.sub(r"[\s_-]+", "", font).casefold() in _CODE_FONTS


def format_code(text: str, language: str | None = None) -> CodeFormatting:
    if not text or len(text) > MAX_CODE_CHARACTERS:
        return _plain(text)
    selected = _language(text, language)
    if selected is None:
        return _plain(text)
    lexer = _LEXERS[selected]()
    result: list[CodeToken] = []
    cursor = 0
    # get_tokens() normalizes CRLF, strips newlines and can expand tabs. The
    # unprocessed API supplies offsets into the unchanged original string.
    for count, (offset, kind, value) in enumerate(lexer.get_tokens_unprocessed(text), start=1):
        if count > MAX_CODE_TOKENS or offset != cursor or not value:
            return _plain(text)
        end = cursor + len(value)
        if text[cursor:end] != value:
            return _plain(text)
        token = _styled_token(value, kind)
        if result and (result[-1].color, result[-1].bold) == (token.color, token.bold):
            previous = result[-1]
            result[-1] = CodeToken(previous.text + value, token.color, token.bold)
        else:
            result.append(token)
        cursor = end
    if cursor != len(text) or "".join(token.text for token in result) != text:
        return _plain(text)
    return CodeFormatting(selected, tuple(result))


def _plain(text: str) -> CodeFormatting:
    return CodeFormatting(None, (CodeToken(text),) if text else ())


def _language(text: str, hint: str | None) -> str | None:
    if hint:
        if len(hint) > _MAX_LANGUAGE_HINT_LENGTH:
            return None
        if hint.strip():
            return _hint_language(hint)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except ValueError, RecursionError:
            return None
        return "json"
    for language, signature in _SIGNATURES:
        if re.search(signature, text):
            return language
    return "yaml" if _looks_like_yaml(text) else None


def _hint_language(hint: str) -> str | None:
    alias = hint.split(maxsplit=1)[0].casefold()
    canonical = _ALIASES.get(alias, alias)
    return canonical if canonical in _LEXERS else None


def _looks_like_yaml(text: str) -> bool:
    # Require multiple concrete mapping values; prose headings alone are not YAML.
    values = [
        line.partition(":")[2].strip() for line in text.splitlines() if _YAML_ENTRY.fullmatch(line)
    ]
    return len(values) >= _MIN_YAML_ENTRIES and all(
        value in {"true", "false", "null"} or re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value)
        for value in values
    )


def _styled_token(text: str, kind: tuple[str, ...]) -> CodeToken:
    if kind[:1] == ("Comment",):
        return CodeToken(text, "62616C")
    if kind[:1] == ("Keyword",):
        return CodeToken(text, "6D28D9", bold=True)
    if kind[:2] in {("Name", "Function"), ("Name", "Class"), ("Name", "Tag")}:
        return CodeToken(text, "6D28D9")
    if kind[:2] in {("Literal", "String"), ("Literal", "Number")}:
        return CodeToken(text, "006D77")
    return CodeToken(text)
