from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, TypeIs
from urllib.parse import urljoin, urlsplit
from xml.dom import Node
from xml.parsers.expat import ExpatError
from zipfile import BadZipFile, ZipFile
import zlib

from defusedxml.common import DefusedXmlException
from defusedxml.minidom import parse


if TYPE_CHECKING:
    from xml.dom.minidom import Document, Element, Node as DOMNode, Text

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_2010_NAMESPACE = "http://schemas.microsoft.com/office/word/2010/wordml"
DOCUMENT_PART = "word/document.xml"
ROOT_RELATIONSHIPS_PART = "_rels/.rels"
DOCUMENT_RELATIONSHIPS_PART = "word/_rels/document.xml.rels"
RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
SETTINGS_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings"
)
MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_PARTS = 2048
MAX_XML_DEPTH = 128
MAX_XML_NODES = 100_000
BODY_FONTS = frozenset({"Arial", "Calibri", "Aptos", "Times New Roman", "Nunito Sans"})
TARGET_FONT = "Nunito Sans"
LATIN_FONT_ATTRIBUTE_COUNT = 2
CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
WORD_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
PRESERVED_RUN_PROPERTIES = frozenset(
    {
        "rFonts",
        "b",
        "bCs",
        "i",
        "iCs",
        "caps",
        "smallCaps",
        "strike",
        "dstrike",
        "outline",
        "shadow",
        "emboss",
        "imprint",
        "noProof",
        "snapToGrid",
        "vanish",
        "webHidden",
        "color",
        "spacing",
        "w",
        "kern",
        "position",
        "sz",
        "szCs",
        "highlight",
        "u",
        "effect",
        "bdr",
        "shd",
        "fitText",
        "vertAlign",
        "rtl",
        "cs",
        "em",
        "lang",
        "eastAsianLayout",
        "specVanish",
        "oMath",
        "rStyle",
    }
)


class UnsafeWordPackageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WordFormatResult:
    content: bytes
    source_sha256: str
    output_sha256: str
    changed_runs: int
    requires_render_review: bool


def format_word_package(content: bytes) -> WordFormatResult:
    parts = _read_parts(content)
    _validate_package_identity(parts)
    document = _parse_xml(parts[DOCUMENT_PART])
    if document.documentElement is None or not _word(document.documentElement, "document"):
        raise UnsafeWordPackageError
    changed = _format_body(document)
    if not changed:
        return WordFormatResult(
            content, _digest(content), _digest(content), 0, requires_render_review=False
        )
    replacement = document.toxml(encoding="UTF-8")
    output = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output, "w") as target:
        target.comment = source.comment
        for entry in source.infolist():
            target.writestr(
                entry, replacement if entry.filename == DOCUMENT_PART else parts[entry.filename]
            )
    rendered = output.getvalue()
    _read_parts(rendered)
    return WordFormatResult(
        rendered, _digest(content), _digest(rendered), changed, requires_render_review=True
    )


def verify_word_package(source: bytes, output: bytes) -> bool:
    return format_word_package(source).content == output


def _digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def _read_parts(content: bytes) -> dict[str, bytes]:
    if len(content) > MAX_PACKAGE_BYTES:
        raise UnsafeWordPackageError
    try:
        with ZipFile(BytesIO(content)) as archive:
            return _checked_parts(archive)
    except (BadZipFile, RuntimeError, NotImplementedError, zlib.error) as error:
        raise UnsafeWordPackageError from error


def _parse_xml(content: bytes) -> Document:
    try:
        document = parse(BytesIO(content), forbid_dtd=True)
        _validate_xml_budget(document)
    except (DefusedXmlException, ExpatError, RecursionError) as error:
        raise UnsafeWordPackageError from error
    return document


def _validate_xml_budget(document: Document) -> None:
    pending = [(document.documentElement, 1)]
    nodes = 0
    while pending:
        node, depth = pending.pop()
        if node is None:
            continue
        nodes += 1 + len(node.childNodes)
        if depth > MAX_XML_DEPTH or nodes > MAX_XML_NODES:
            raise UnsafeWordPackageError
        pending.extend((child, depth + 1) for child in _elements(node))


def _validate_package_identity(parts: dict[str, bytes]) -> None:
    _validate_main_relationship(parts)
    types = _parse_xml(parts["[Content_Types].xml"])
    root = types.documentElement
    if root is None or root.namespaceURI != CONTENT_TYPES_NAMESPACE or root.localName != "Types":
        raise UnsafeWordPackageError
    main = [
        node for node in _elements(root) if node.getAttribute("PartName") == "/word/document.xml"
    ]
    if (
        len(main) != 1
        or main[0].namespaceURI != CONTENT_TYPES_NAMESPACE
        or main[0].localName != "Override"
        or main[0].getAttribute("ContentType") != WORD_MAIN_TYPE
    ):
        raise UnsafeWordPackageError
    for settings_part in _settings_parts(parts):
        settings = _parse_xml(parts[settings_part])
        if (
            settings.documentElement is None
            or not _word(settings.documentElement, "settings")
            or settings.getElementsByTagNameNS(WORD_NAMESPACE, "documentProtection")
        ):
            raise UnsafeWordPackageError


def _settings_parts(parts: dict[str, bytes]) -> set[str]:
    result = {"word/settings.xml"} if "word/settings.xml" in parts else set()
    if DOCUMENT_RELATIONSHIPS_PART not in parts:
        return result
    root = _parse_xml(parts[DOCUMENT_RELATIONSHIPS_PART]).documentElement
    settings = [
        node for node in _relationships(root) if node.getAttribute("Type") == SETTINGS_RELATIONSHIP
    ]
    if len(settings) > 1:
        raise UnsafeWordPackageError
    if not settings:
        return result
    target = _internal_target(settings[0], f"/{DOCUMENT_PART}")
    part = target.removeprefix("/")
    if not target.startswith("/") or target.startswith("//") or part not in parts:
        raise UnsafeWordPackageError
    result.add(part)
    return result


def _validate_main_relationship(parts: dict[str, bytes]) -> None:
    if ROOT_RELATIONSHIPS_PART not in parts:
        raise UnsafeWordPackageError
    root = _parse_xml(parts[ROOT_RELATIONSHIPS_PART]).documentElement
    main = [
        node
        for node in _relationships(root)
        if node.getAttribute("Type") == OFFICE_DOCUMENT_RELATIONSHIP
    ]
    if len(main) != 1 or _internal_target(main[0], "/") != f"/{DOCUMENT_PART}":
        raise UnsafeWordPackageError


def _relationships(root: Element | None) -> list[Element]:
    if (
        root is None
        or root.namespaceURI != RELATIONSHIPS_NAMESPACE
        or root.localName != "Relationships"
    ):
        raise UnsafeWordPackageError
    relationships = _elements(root)
    if any(
        node.namespaceURI != RELATIONSHIPS_NAMESPACE or node.localName != "Relationship"
        for node in relationships
    ):
        raise UnsafeWordPackageError
    identifiers = [node.getAttribute("Id") for node in relationships]
    if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(
        identifiers
    ):
        raise UnsafeWordPackageError
    return relationships


def _internal_target(relationship: Element, base: str) -> str:
    target = relationship.getAttribute("Target")
    try:
        resolved_target = urljoin(base, target)
        parsed = urlsplit(resolved_target)
    except ValueError as error:
        raise UnsafeWordPackageError from error
    if any((parsed.scheme, parsed.netloc, parsed.query, parsed.fragment)):
        raise UnsafeWordPackageError
    if (
        relationship.getAttribute("TargetMode") not in {"", "Internal"}
        or _elements(relationship)
        or any(character.isspace() or not character.isprintable() for character in target)
        or not target
        or "%" in target
    ):
        raise UnsafeWordPackageError
    return resolved_target


def _checked_parts(archive: ZipFile) -> dict[str, bytes]:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(entries) > MAX_PARTS or len(set(names)) != len(names):
        raise UnsafeWordPackageError
    if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        raise UnsafeWordPackageError
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in entry.filename
            or entry.flag_bits & 1
        ):
            raise UnsafeWordPackageError
        if entry.filename.startswith("_xmlsignatures/") or entry.filename.lower().endswith(
            "vbaproject.bin"
        ):
            raise UnsafeWordPackageError
    if DOCUMENT_PART not in names or "[Content_Types].xml" not in names:
        raise UnsafeWordPackageError
    return {entry.filename: archive.read(entry) for entry in entries}


def _elements(node: Document | Element) -> list[Element]:
    return [child for child in node.childNodes if _is_element(child)]


def _is_element(node: DOMNode) -> TypeIs[Element]:
    return node.nodeType == Node.ELEMENT_NODE


def _word(node: Element, name: str) -> bool:
    return node.namespaceURI == WORD_NAMESPACE and node.localName == name


def _format_body(document: Document) -> int:
    if document.documentElement is None:
        raise UnsafeWordPackageError
    bodies = [node for node in _elements(document.documentElement) if _word(node, "body")]
    if len(bodies) != 1:
        raise UnsafeWordPackageError
    changed = 0
    field_depth = 0
    for paragraph in _elements(bodies[0]):
        fields = paragraph.getElementsByTagNameNS(WORD_NAMESPACE, "fldChar")
        inside_field = field_depth > 0 or bool(fields)
        for field in fields:
            kind = field.getAttributeNS(WORD_NAMESPACE, "fldCharType")
            if kind == "begin":
                field_depth += 1
            elif kind == "end" and field_depth:
                field_depth -= 1
            elif kind != "separate" or not field_depth:
                raise UnsafeWordPackageError
        if inside_field:
            continue
        if not _word(paragraph, "p"):
            continue
        children = _elements(paragraph)
        if any(not (_word(child, "pPr") or _word(child, "r")) for child in children):
            continue
        for run in children:
            if _word(run, "r"):
                changed += _format_run(run)
    if field_depth:
        raise UnsafeWordPackageError
    return changed


def _format_run(run: Element) -> int:
    children = _elements(run)
    if any(not (_word(child, "rPr") or _word(child, "t")) for child in children):
        return 0
    texts = [child for child in children if _word(child, "t")]
    text = "".join(node.data for child in texts for node in child.childNodes if _is_text(node))
    if not text.strip() or not text.isascii():
        return 0
    properties = [child for child in children if _word(child, "rPr")]
    if len(properties) != 1:
        return 0
    if any(not _preserved_run_property(run_property) for run_property in _elements(properties[0])):
        return 0
    fonts = [child for child in _elements(properties[0]) if _word(child, "rFonts")]
    if len(fonts) != 1:
        return 0
    font = fonts[0]
    attributes = list(font.attributes.values())
    if any(
        attribute.namespaceURI != WORD_NAMESPACE
        or attribute.localName not in {"ascii", "hAnsi", "cs", "eastAsia", "hint"}
        for attribute in attributes
    ):
        return 0
    if (
        font.hasAttributeNS(WORD_NAMESPACE, "hint")
        and font.getAttributeNS(WORD_NAMESPACE, "hint") != "default"
    ):
        return 0
    latin = [
        attribute
        for attribute in attributes
        if attribute.namespaceURI == WORD_NAMESPACE and attribute.localName in {"ascii", "hAnsi"}
    ]
    if len(latin) != LATIN_FONT_ATTRIBUTE_COUNT or any(
        attribute.value not in BODY_FONTS for attribute in latin
    ):
        return 0
    if all(attribute.value == TARGET_FONT for attribute in latin):
        return 0
    for attribute in latin:
        attribute.value = TARGET_FONT
    return 1


def _is_text(node: DOMNode) -> TypeIs[Text]:
    return node.nodeType == Node.TEXT_NODE


def _preserved_run_property(run_property: Element) -> bool:
    if run_property.namespaceURI == WORD_NAMESPACE:
        return run_property.localName in PRESERVED_RUN_PROPERTIES and not _elements(run_property)
    if run_property.namespaceURI != WORD_2010_NAMESPACE or run_property.localName not in {
        "textFill",
        "textOutline",
    }:
        return False
    return all(
        node.namespaceURI == WORD_2010_NAMESPACE
        and node.localName in {"solidFill", "srgbClr", "noFill", "miter"}
        for node in run_property.getElementsByTagName("*")
    )
