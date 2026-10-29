from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarkdownImportInline:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    url: str | None = None


@dataclass(frozen=True, slots=True)
class MarkdownImportBlock:
    kind: str
    text: str = ""
    level: int = 0
    ordered: bool = False
    start: int = 1
    list_group: int = 0
    rows: tuple[tuple[str, ...], ...] = ()
    links: tuple[tuple[str, str], ...] = ()
    bold: tuple[str, ...] = ()
    italic: tuple[str, ...] = ()
    inline_code: tuple[str, ...] = ()
    inline_signature: tuple[MarkdownImportInline, ...] = ()
    cell_signatures: tuple[tuple[tuple[MarkdownImportInline, ...], ...], ...] = ()
    checked: bool | None = None


@dataclass(frozen=True, slots=True)
class MarkdownImportManifest:
    blocks: tuple[MarkdownImportBlock, ...]
    branded: bool
    header_title: str | None = None
