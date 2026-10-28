from io import BytesIO
from zipfile import ZipFile

import pytest

from app.document_transform import word_package
from app.document_transform.word_package import (
    CONTENT_TYPES_NAMESPACE,
    WORD_MAIN_TYPE,
    UnsafeWordPackageError,
    format_word_package,
    verify_word_package,
)


NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RUN = '<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="60"/></w:rPr><w:t>Authored Title</w:t></w:r>'


def package(body: str) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(word_package.ROOT_RELATIONSHIPS_PART, root_relationships())
        archive.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{CONTENT_TYPES_NAMESPACE}"><Override PartName="/word/document.xml" ContentType="{WORD_MAIN_TYPE}"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{NAMESPACE}" xmlns:mc="urn:compat" mc:Ignorable="x" xmlns:x="urn:custom"><w:body>{body}</w:body></w:document>',
        )
        archive.writestr("word/header1.xml", "exact authored furniture")
        archive.writestr(
            word_package.DOCUMENT_RELATIONSHIPS_PART,
            f'<Relationships xmlns="{word_package.RELATIONSHIPS_NAMESPACE}"/>',
        )
    return output.getvalue()


def root_relationships(target: str = "word/document.xml", mode: str = "Internal") -> str:
    return f'<Relationships xmlns="{word_package.RELATIONSHIPS_NAMESPACE}"><Relationship Id="rId1" Type="{word_package.OFFICE_DOCUMENT_RELATIONSHIP}" Target="{target}" TargetMode="{mode}"/></Relationships>'


def test_format_is_exactly_idempotent_and_preserves_other_parts():
    source = package(f"<w:p>{RUN}</w:p><w:tbl><w:tr><w:tc><w:p>{RUN}</w:p></w:tc></w:tr></w:tbl>")
    result = format_word_package(source)
    assert result.changed_runs == 1
    assert result.requires_render_review
    assert verify_word_package(source, result.content)
    assert format_word_package(result.content).content == result.content
    with ZipFile(BytesIO(source)) as before, ZipFile(BytesIO(result.content)) as after:
        for name in before.namelist():
            if name != "word/document.xml":
                assert before.read(name) == after.read(name)
        xml = after.read("word/document.xml")
        assert b'mc:Ignorable="x"' in xml
        assert b'xmlns:x="urn:custom"' in xml
        assert b'<w:b/><w:sz w:val="60"/>' in xml
        assert xml.count(b"Arial") == 2
        assert xml.count(b"Nunito Sans") == 2


@pytest.mark.parametrize(
    "body",
    [
        f"<w:p><w:hyperlink>{RUN}</w:hyperlink></w:p>",
        f'<w:p><w:fldSimple w:instr="DATE">{RUN}</w:fldSimple></w:p>',
        f"<w:p>{RUN.replace('Arial', 'Courier New')}</w:p>",
        f"<w:p>{RUN.replace('Authored Title', 'العنوان')}</w:p>",
        f'<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p><w:p>{RUN}</w:p><w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>',
    ],
)
def test_protected_or_ambiguous_runs_are_unchanged(body: str):
    source = package(body)
    result = format_word_package(source)
    assert result.content == source
    assert result.changed_runs == 0


def test_fields_do_not_disable_unrelated_body_and_bad_fields_fail_closed():
    field = (
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )
    assert format_word_package(package(f"{field}<w:p>{RUN}</w:p>")).changed_runs == 1
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(package(field.replace('w:fldCharType="end"', 'w:fldCharType="begin"')))


def test_named_styles_and_non_latin_family_attributes_remain_exact():
    run = RUN.replace(
        'w:hAnsi="Arial"',
        'w:hAnsi="Arial" w:cs="Arabic Font" w:eastAsia="CJK Font" w:hint="default"',
    )
    source = package(f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>{run}</w:p>')
    result = format_word_package(source)
    assert result.changed_runs == 1
    with ZipFile(BytesIO(result.content)) as archive:
        xml = archive.read("word/document.xml")
        assert b'w:pStyle w:val="Title"' in xml
        assert b'w:cs="Arabic Font" w:eastAsia="CJK Font" w:hint="default"' in xml
        assert b'w:sz w:val="60"' in xml


def test_readback_rejects_any_package_tampering():
    source = package(f"<w:p>{RUN}</w:p>")
    assert not verify_word_package(source, source)
    result = format_word_package(source)
    assert not verify_word_package(source, result.content + b"unexpected")


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "word\\bad", "_xmlsignatures/sig.xml", "word/vbaProject.bin"]
)
def test_unsafe_package_members_are_rejected(name: str):
    output = BytesIO(package(f"<w:p>{RUN}</w:p>"))
    with ZipFile(output, "a") as archive:
        archive.writestr(name, "bad")
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(output.getvalue())


def replace_part(source: bytes, name: str, value: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(BytesIO(source)) as original, ZipFile(output, "w") as target:
        for entry in original.infolist():
            target.writestr(entry, value if entry.filename == name else original.read(entry))
        if name not in original.namelist():
            target.writestr(name, value)
    return output.getvalue()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("word/document.xml", f'<w:wrong xmlns:w="{NAMESPACE}"><w:body/></w:wrong>'.encode()),
        ("word/document.xml", b'<!DOCTYPE document [<!ENTITY bad "payload">]><document/>'),
        ("word/document.xml", b"<unclosed>"),
        ("[Content_Types].xml", b"<Types/>"),
        (
            "word/settings.xml",
            f'<w:settings xmlns:w="{NAMESPACE}"><w:documentProtection w:enforcement="0"/></w:settings>'.encode(),
        ),
    ],
)
def test_bad_identity_xml_and_protection_are_typed_errors(name: str, value: bytes):
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(replace_part(package(f"<w:p>{RUN}</w:p>"), name, value))


def test_corrupt_crc_and_unsupported_compression_are_typed_errors():
    source = package(f"<w:p>{RUN}</w:p>")
    corrupt = source.replace(b"exact authored furniture", b"wrong authored furniture")
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(corrupt)
    unsupported = bytearray(source)
    central = source.index(b"PK\x01\x02")
    unsupported[central + 10 : central + 12] = (99).to_bytes(2, "little")
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(bytes(unsupported))


@pytest.mark.parametrize("attribute", ['w:asciiTheme="minorHAnsi"', 'w:hint="eastAsia"'])
def test_theme_or_complex_script_font_selection_is_preserved(attribute: str):
    source = package(f"<w:p>{RUN.replace('<w:rFonts', '<w:rFonts ' + attribute)}</w:p>")
    assert format_word_package(source).content == source


def test_deep_xml_is_rejected_before_recursive_dom_operations():
    source = package("<w:tbl>" * 1200 + f"<w:p>{RUN}</w:p>" + "</w:tbl>" * 1200)
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(source)


def test_xml_node_budget_is_enforced(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(word_package, "MAX_XML_NODES", 30)
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(package(f"<w:p>{RUN}</w:p>" * 10))


@pytest.mark.parametrize(
    "run_property",
    [
        "<w:rPrChange><w:rPr><w:b/></w:rPr></w:rPrChange>",
        '<x:protected xmlns:x="urn:protected"/>',
        '<w:b><x:protected xmlns:x="urn:protected"/></w:b>',
    ],
)
def test_tracked_unknown_or_nested_run_properties_remain_exact(run_property: str):
    source = package(f"<w:p>{RUN.replace('</w:rPr>', run_property + '</w:rPr>')}</w:p>")
    assert format_word_package(source).content == source


def test_word_2010_fill_outline_payloads_remain_exact():
    presentation = f'<w14:textFill xmlns:w14="{word_package.WORD_2010_NAMESPACE}"><w14:solidFill><w14:srgbClr w14:val="0000FF"/></w14:solidFill></w14:textFill><w14:textOutline xmlns:w14="{word_package.WORD_2010_NAMESPACE}" w14:w="0"><w14:noFill/><w14:miter/></w14:textOutline>'
    source = package(f"<w:p>{RUN.replace('</w:rPr>', presentation + '</w:rPr>')}</w:p>")
    result = format_word_package(source)
    assert result.changed_runs == 1
    with ZipFile(BytesIO(result.content)) as archive:
        assert presentation.encode() in archive.read("word/document.xml")


def test_output_must_also_fit_the_package_budget(monkeypatch: pytest.MonkeyPatch):
    source = package(f"<w:p>{RUN}</w:p>")
    monkeypatch.setattr(word_package, "MAX_PACKAGE_BYTES", len(source))
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(source)


@pytest.mark.parametrize(
    "target", ["word/document.xml", "/word/document.xml", "./word/document.xml"]
)
def test_root_relationship_resolves_to_the_formatted_part(target: str):
    source = replace_part(
        package(f"<w:p>{RUN}</w:p>"),
        word_package.ROOT_RELATIONSHIPS_PART,
        root_relationships(target).encode(),
    )
    assert format_word_package(source).changed_runs == 1


@pytest.mark.parametrize(
    "relationships",
    [
        root_relationships("word/other.xml"),
        root_relationships("https://example.com/word/document.xml"),
        root_relationships("https://["),
        root_relationships(mode="External"),
        root_relationships(mode="Unknown"),
        root_relationships().replace('officeDocument"', 'unknown"'),
        root_relationships().replace(
            "</Relationships>",
            f'<Relationship Id="rId2" Type="{word_package.OFFICE_DOCUMENT_RELATIONSHIP}" Target="word/document.xml"/></Relationships>',
        ),
        root_relationships().replace(
            "</Relationships>",
            '<Relationship Id="rId1" Type="other" Target="other.xml"/></Relationships>',
        ),
        root_relationships().replace(word_package.RELATIONSHIPS_NAMESPACE, "urn:wrong"),
    ],
    ids=[
        "different-part",
        "external-url",
        "invalid-uri",
        "external-mode",
        "unknown-mode",
        "missing-main",
        "duplicate-main",
        "duplicate-id",
        "wrong-namespace",
    ],
)
def test_invalid_main_relationship_fails_closed(relationships: str):
    source = replace_part(
        package(f"<w:p>{RUN}</w:p>"), word_package.ROOT_RELATIONSHIPS_PART, relationships.encode()
    )
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(source)


def test_missing_root_relationships_fails_closed():
    source = package(f"<w:p>{RUN}</w:p>")
    output = BytesIO()
    with ZipFile(BytesIO(source)) as original, ZipFile(output, "w") as target:
        for entry in original.infolist():
            if entry.filename != word_package.ROOT_RELATIONSHIPS_PART:
                target.writestr(entry, original.read(entry))
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(output.getvalue())


def settings_package(target: str, *, protected: bool, mode: str = "Internal") -> bytes:
    source = package(f"<w:p>{RUN}</w:p>")
    relationships = root_relationships(target, mode).replace(
        word_package.OFFICE_DOCUMENT_RELATIONSHIP, word_package.SETTINGS_RELATIONSHIP
    )
    source = replace_part(source, word_package.DOCUMENT_RELATIONSHIPS_PART, relationships.encode())
    settings = (
        f'<w:settings xmlns:w="{NAMESPACE}">'
        + (
            '<w:documentProtection w:enforcement="1" w:edit="readOnly"/>'
            if protected
            else '<w:zoom w:percent="100"/>'
        )
        + "</w:settings>"
    )
    source = replace_part(source, "word/customSettings.xml", settings.encode())
    with ZipFile(BytesIO(source)) as archive:
        types = archive.read("[Content_Types].xml")
    return replace_part(
        source,
        "[Content_Types].xml",
        types.replace(
            b"</Types>",
            b'<Override PartName="/word/customSettings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/></Types>',
        ),
    )


@pytest.mark.parametrize(
    "target", ["customSettings.xml", "/word/customSettings.xml", "../word/customSettings.xml"]
)
def test_relocated_protected_settings_are_rejected(target: str):
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(settings_package(target, protected=True))


def test_relocated_unprotected_settings_are_preserved_exactly():
    source = settings_package("customSettings.xml", protected=False)
    result = format_word_package(source)
    assert result.changed_runs == 1
    with ZipFile(BytesIO(source)) as before, ZipFile(BytesIO(result.content)) as after:
        assert before.read("word/customSettings.xml") == after.read("word/customSettings.xml")
        assert before.read(word_package.DOCUMENT_RELATIONSHIPS_PART) == after.read(
            word_package.DOCUMENT_RELATIONSHIPS_PART
        )


@pytest.mark.parametrize(
    ("target", "mode"),
    [
        ("missing.xml", "Internal"),
        ("customSettings.xml", "External"),
        ("https://[", "Internal"),
        ("https://example.com/settings.xml", "Internal"),
        ("customSettings.xml#fragment", "Internal"),
    ],
    ids=["missing-part", "external-mode", "malformed-uri", "external-url", "fragment"],
)
def test_unsafe_settings_relationship_fails_closed(target: str, mode: str):
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(settings_package(target, protected=False, mode=mode))


def test_duplicate_settings_relationship_fails_closed():
    source = settings_package("customSettings.xml", protected=False)
    with ZipFile(BytesIO(source)) as archive:
        relationships = archive.read(word_package.DOCUMENT_RELATIONSHIPS_PART)
    duplicate = f'<Relationship Id="rId2" Type="{word_package.SETTINGS_RELATIONSHIP}" Target="customSettings.xml"/></Relationships>'.encode()
    source = replace_part(
        source,
        word_package.DOCUMENT_RELATIONSHIPS_PART,
        relationships.replace(b"</Relationships>", duplicate),
    )
    with pytest.raises(UnsafeWordPackageError):
        format_word_package(source)
