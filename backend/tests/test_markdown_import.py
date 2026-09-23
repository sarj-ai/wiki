from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.document_transform.brand_manifest import (
    CURRENT_BRAND_MANIFEST,
    trusted_document_logo_description,
)
from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.gateway import OutputDraft, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_markdown
from app.document_transform.markdown_import import verify_markdown_import
from app.document_transform.schemas import DocumentOutput
from app.identifiers import DocsRevisionId, DocsTabId, FileId


def _paragraph(
    runs: list[tuple[str, dict[str, Any]]],
    *,
    style: dict[str, Any] | None = None,
    bullet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paragraph: dict[str, Any] = {
        "elements": [
            {"textRun": {"content": content, "textStyle": text_style}}
            for content, text_style in runs
        ],
        "paragraphStyle": style or {"namedStyleType": "NORMAL_TEXT"},
    }
    if bullet is not None:
        paragraph["bullet"] = bullet
    return {"paragraph": paragraph}


def _converted_document() -> dict[str, Any]:
    return {
        "revisionId": "revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-1"},
                "childTabs": [],
                "documentTab": {
                    "lists": {
                        "ordered-5": {
                            "listProperties": {
                                "nestingLevels": [{"glyphType": "DECIMAL", "startNumber": 5}]
                            }
                        },
                        "ordered-1": {
                            "listProperties": {
                                "nestingLevels": [{"glyphType": "DECIMAL", "startNumber": 1}]
                            }
                        },
                    },
                    "body": {
                        "content": [
                            _paragraph([("Import", {})]),
                            _paragraph(
                                [("Heading", {})],
                                style={"namedStyleType": "HEADING_1"},
                            ),
                            _paragraph(
                                [
                                    ("Bold", {"bold": True}),
                                    (", ", {}),
                                    ("italic", {"italic": True}),
                                    (", ", {}),
                                    (
                                        "link",
                                        {"link": {"url": "https://sarj.ai"}},
                                    ),
                                    (", and ", {}),
                                    (
                                        "code",
                                        {"weightedFontFamily": {"fontFamily": "Courier New"}},
                                    ),
                                    (".\n", {}),
                                ]
                            ),
                            _paragraph(
                                [("Fifth\n", {})],
                                bullet={"listId": "ordered-5", "nestingLevel": 0},
                            ),
                            _paragraph(
                                [("Sixth\n", {})],
                                bullet={"listId": "ordered-5", "nestingLevel": 0},
                            ),
                            _paragraph([("Break\n", {})]),
                            _paragraph(
                                [("Restarted\n", {})],
                                bullet={"listId": "ordered-1", "nestingLevel": 0},
                            ),
                            {
                                "table": {
                                    "tableRows": [
                                        {
                                            "tableCells": [
                                                {"content": [_paragraph([("Owner\n", {})])]},
                                                {"content": [_paragraph([("Status\n", {})])]},
                                            ]
                                        },
                                        {
                                            "tableCells": [
                                                {"content": [_paragraph([("Wiki\n", {})])]},
                                                {"content": [_paragraph([("Ready\n", {})])]},
                                            ]
                                        },
                                    ]
                                }
                            },
                        ]
                    },
                },
            }
        ],
    }


def _rgb(value: str) -> dict[str, Any]:
    return {
        "color": {
            "rgbColor": {
                "red": int(value[1:3], 16) / 255,
                "green": int(value[3:5], 16) / 255,
                "blue": int(value[5:7], 16) / 255,
            }
        }
    }


def _brand_run_style(*, size: int, color: str, bold: bool = False) -> dict[str, Any]:
    style: dict[str, Any] = {
        "weightedFontFamily": {"fontFamily": CURRENT_BRAND_MANIFEST.document.font_family},
        "fontSize": {"magnitude": size, "unit": "PT"},
        "foregroundColor": _rgb(color),
    }
    if bold:
        style["bold"] = True
    return style


def _brand_paragraph_style() -> dict[str, Any]:
    tokens = CURRENT_BRAND_MANIFEST.document
    return {
        "namedStyleType": "NORMAL_TEXT",
        "spaceAbove": {"magnitude": tokens.paragraph_space_above_pt, "unit": "PT"},
        "spaceBelow": {"magnitude": tokens.paragraph_space_below_pt, "unit": "PT"},
        "lineSpacing": tokens.line_spacing_percent,
    }


def test_normalized_manifest_verifies_content_lists_tables_and_inline_semantics() -> None:
    markdown = """# Heading

**Bold**, *italic*, [link](https://sarj.ai), and `code`.

5. Fifth
6. Sixth

Break

1. Restarted

| Owner | Status |
| --- | --- |
| Wiki | Ready |
"""
    manifest = markdown_import_manifest(markdown, title="Import", branded=False)

    document = _converted_document()
    del document["tabs"][0]["documentTab"]["body"]["content"][0]
    verification = verify_markdown_import(document, manifest)

    assert verification.content_preserved is True
    assert verification.structure_preserved is True
    assert verification.brand_applied is True


@pytest.mark.parametrize(
    "markdown",
    [
        "```text\n[^literal]\n```",
        "~~~\n[^literal]\n~~~",
        "    [^literal]",
        "Escaped \\[^literal]",
        "Literal `[^literal]`",
    ],
)
def test_literal_footnote_syntax_in_code_or_escaped_text_is_importable(markdown: str) -> None:
    assert markdown_import_manifest(markdown, title="Literal", branded=False).blocks


def test_actual_footnote_syntax_remains_unsupported() -> None:
    with pytest.raises(UnsupportedSourceError):
        markdown_import_manifest("Text[^note]", title="Footnote", branded=False)


@pytest.mark.parametrize(
    ("markdown", "kind", "text"),
    [
        ("- [ ] Task", "check", "Task"),
        ("- [x] Done", "check", "Done"),
        ("- `[ ] Task`", "list", "[ ] Task"),
        ("- **[x] Done**", "list", "[x] Done"),
    ],
)
def test_checklist_marker_is_only_consumed_when_it_is_plain_markdown(
    markdown: str, kind: str, text: str
) -> None:
    manifest = markdown_import_manifest(markdown, title="List", branded=False)
    assert manifest.blocks[-1].kind == kind
    assert manifest.blocks[-1].text == text


@pytest.mark.parametrize("extra_title", [False, True])
def test_authored_leading_heading_manifest_requires_exact_native_readback(
    extra_title: bool,
) -> None:
    manifest = markdown_import_manifest(
        "# *Authored*\n\nBody", title="Generated name", branded=False
    )
    content = [
        _paragraph([("Authored", {"italic": True})], style={"namedStyleType": "HEADING_1"}),
        _paragraph([("Body", {})]),
    ]
    if extra_title:
        content.insert(0, _paragraph([("Generated name", {})]))
    native = {
        "tabs": [{"tabProperties": {"tabId": "tab"}, "documentTab": {"body": {"content": content}}}]
    }
    verified = verify_markdown_import(native, manifest)
    assert verified.content_preserved is (not extra_title)
    assert verified.structure_preserved is (not extra_title)


def test_manifest_distinguishes_content_and_structure_mismatches() -> None:
    markdown = "5. Fifth\n6. Sixth"
    manifest = markdown_import_manifest(markdown, title="Import", branded=False)
    document = _converted_document()
    document["tabs"][0]["documentTab"]["body"]["content"] = (
        document["tabs"][0]["documentTab"]["body"]["content"][:1]
        + document["tabs"][0]["documentTab"]["body"]["content"][3:5]
    )

    wrong_structure = deepcopy(document)
    del wrong_structure["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["bullet"]
    structure_verification = verify_markdown_import(wrong_structure, manifest)
    assert structure_verification.content_preserved is True
    assert structure_verification.structure_preserved is False

    wrong_content = deepcopy(document)
    wrong_content["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"][0][
        "textRun"
    ]["content"] = "Changed\n"
    content_verification = verify_markdown_import(wrong_content, manifest)
    assert content_verification.content_preserved is False


def _branded_document() -> dict[str, Any]:
    return {
        "revisionId": "revision-1",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-1"},
                "childTabs": [],
                "documentTab": {
                    "documentStyle": {
                        "marginLeft": {"magnitude": 72, "unit": "PT"},
                        "marginRight": {"magnitude": 72, "unit": "PT"},
                        "marginTop": {"magnitude": 72, "unit": "PT"},
                        "marginBottom": {"magnitude": 72, "unit": "PT"},
                    },
                    "headers": {
                        "header-1": {
                            "content": [
                                {
                                    "paragraph": {
                                        "paragraphStyle": {
                                            "namedStyleType": "NORMAL_TEXT",
                                            "alignment": "END",
                                            "lineSpacing": 100,
                                            "spaceAbove": {"magnitude": 0, "unit": "PT"},
                                            "spaceBelow": {"magnitude": 0, "unit": "PT"},
                                            "keepWithNext": False,
                                        },
                                        "elements": [
                                            {
                                                "textRun": {
                                                    "content": "Import\u00a0\u00a0\u00a0\u00a0\t",
                                                    "textStyle": {
                                                        "weightedFontFamily": {
                                                            "fontFamily": "Nunito"
                                                        },
                                                        "fontSize": {"magnitude": 9, "unit": "PT"},
                                                        "bold": True,
                                                        "foregroundColor": {
                                                            "color": {
                                                                "rgbColor": {
                                                                    "red": 115 / 255,
                                                                    "green": 115 / 255,
                                                                    "blue": 115 / 255,
                                                                }
                                                            }
                                                        },
                                                    },
                                                }
                                            },
                                            {"inlineObjectElement": {"inlineObjectId": "logo-1"}},
                                            {"textRun": {"content": "\n"}},
                                        ],
                                    }
                                }
                            ]
                        }
                    },
                    "inlineObjects": {
                        "logo-1": {
                            "inlineObjectProperties": {
                                "embeddedObject": {
                                    "title": "Sarj logo",
                                    "description": trusted_document_logo_description(),
                                    "size": {
                                        "width": {
                                            "magnitude": 51.8143939394,
                                            "unit": "PT",
                                        },
                                        "height": {"magnitude": 28, "unit": "PT"},
                                    },
                                }
                            }
                        }
                    },
                    "footers": {
                        "footer-1": {
                            "content": [
                                {
                                    "paragraph": {
                                        "elements": [
                                            {"textRun": {"content": "Sarj · ", "textStyle": {}}},
                                            {"autoText": {"type": "PAGE_NUMBER"}},
                                        ]
                                    }
                                }
                            ]
                        }
                    },
                    "body": {
                        "content": [
                            _paragraph(
                                [
                                    (
                                        "Import\n",
                                        _brand_run_style(
                                            size=CURRENT_BRAND_MANIFEST.document.h1_size_pt,
                                            color=CURRENT_BRAND_MANIFEST.colors.primary,
                                            bold=True,
                                        ),
                                    )
                                ],
                                style=_brand_paragraph_style(),
                            ),
                            _paragraph(
                                [
                                    (
                                        "Body\n",
                                        _brand_run_style(
                                            size=CURRENT_BRAND_MANIFEST.document.body_size_pt,
                                            color=CURRENT_BRAND_MANIFEST.colors.ink,
                                        ),
                                    )
                                ],
                                style=_brand_paragraph_style(),
                            ),
                        ]
                    },
                },
            }
        ],
    }


def test_branded_manifest_requires_margins_logo_footer_and_nunito() -> None:
    manifest = markdown_import_manifest("Body", title="Import", branded=True)
    document = _branded_document()

    assert verify_markdown_import(document, manifest).brand_applied is True
    document["tabs"][0]["documentTab"]["headers"] = {}
    assert verify_markdown_import(document, manifest).brand_applied is False


def test_exact_inline_signatures_reject_extra_style_and_allow_run_fragmentation() -> None:
    markdown = """# Heading

**Bold**, *italic*, [link](https://sarj.ai), and `code`.

5. Fifth
6. Sixth

Break

1. Restarted

| Owner | Status |
| --- | --- |
| Wiki | Ready |
"""
    manifest = markdown_import_manifest(markdown, title="Import", branded=False)

    extra_bold = _converted_document()
    extra_bold["tabs"][0]["documentTab"]["body"]["content"][2]["paragraph"]["elements"][1][
        "textRun"
    ]["textStyle"]["bold"] = True
    del extra_bold["tabs"][0]["documentTab"]["body"]["content"][0]
    assert verify_markdown_import(extra_bold, manifest).structure_preserved is False

    fragmented = _converted_document()
    elements = fragmented["tabs"][0]["documentTab"]["body"]["content"][2]["paragraph"]["elements"]
    elements[0:1] = [
        {"textRun": {"content": "Bo", "textStyle": {"bold": True}}},
        {"textRun": {"content": "ld", "textStyle": {"bold": True}}},
    ]
    elements[5:6] = [
        {"textRun": {"content": "li", "textStyle": {"link": {"url": "https://sarj.ai"}}}},
        {"textRun": {"content": "nk", "textStyle": {"link": {"url": "https://sarj.ai"}}}},
    ]
    del fragmented["tabs"][0]["documentTab"]["body"]["content"][0]
    verification = verify_markdown_import(fragmented, manifest)
    assert verification.content_preserved is True
    assert verification.structure_preserved is True


@pytest.mark.parametrize("extra_kind", ["tab", "inline", "positioned"])
def test_unexpected_google_doc_topology_fails_closed(extra_kind: str) -> None:
    manifest = markdown_import_manifest("Body", title="Import", branded=False)
    document: dict[str, Any] = {
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-1"},
                "childTabs": [],
                "documentTab": {
                    "body": {
                        "content": [_paragraph([("Import\n", {})]), _paragraph([("Body\n", {})])]
                    }
                },
            }
        ]
    }
    match extra_kind:
        case "tab":
            document["tabs"].append(
                {
                    "tabProperties": {"tabId": "extra"},
                    "childTabs": [],
                    "documentTab": {"body": {"content": []}},
                }
            )
        case "inline":
            document["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"][
                "elements"
            ].append({"inlineObjectElement": {"inlineObjectId": "foreign"}})
        case _:
            document["tabs"][0]["documentTab"]["positionedObjects"] = {"foreign": {}}
    assert verify_markdown_import(document, manifest).structure_preserved is False


@pytest.mark.parametrize(
    "malformed_run",
    [
        pytest.param({"textStyle": {}}, id="missing-content"),
        pytest.param({"content": 42}, id="non-text-content"),
        pytest.param({"content": "", "textStyle": "bold"}, id="non-object-style"),
        pytest.param(
            {"content": "", "suggestedInsertionIds": ["unaccepted"]},
            id="unaccepted-suggestion",
        ),
    ],
)
def test_import_readback_rejects_malformed_or_unaccepted_text_runs(
    malformed_run: dict[str, Any],
) -> None:
    manifest = markdown_import_manifest("Body", title="Import", branded=False)
    title = _paragraph([("Import\n", {})])
    body = _paragraph([("Body\n", {})])
    body["paragraph"]["elements"].append({"textRun": malformed_run})
    document = {"tabs": [{"documentTab": {"body": {"content": [title, body]}}}]}

    result = verify_markdown_import(document, manifest)

    assert result.content_preserved is True
    assert result.structure_preserved is False


@pytest.mark.parametrize("span", ["rowSpan", "columnSpan"])
def test_import_readback_rejects_merged_table_cells(span: str) -> None:
    manifest = markdown_import_manifest("| A |\n| --- |\n| B |", title="Import", branded=False)
    cell = {"content": [_paragraph([("A\n", {})])], span: 2}
    second = {"content": [_paragraph([("B\n", {})])]}
    document = {
        "tabs": [
            {
                "documentTab": {
                    "body": {
                        "content": [
                            _paragraph([("Import\n", {})]),
                            {
                                "table": {
                                    "tableRows": [
                                        {"tableCells": [cell]},
                                        {"tableCells": [second]},
                                    ]
                                }
                            },
                        ]
                    }
                }
            }
        ]
    }

    result = verify_markdown_import(document, manifest)

    assert result.content_preserved is True
    assert result.structure_preserved is False


@pytest.mark.parametrize("indexed_run", [False, True])
@pytest.mark.parametrize(
    ("fields", "accepted"),
    [
        ({"startIndex": 1, "endIndex": 8}, True),
        ({"endIndex": 8}, True),
        ({"startIndex": 0, "endIndex": 8}, True),
        ({"startIndex": True}, False),
        ({"endIndex": "8"}, False),
        ({"startIndex": -1}, False),
        ({"startIndex": 8, "endIndex": 1}, False),
        ({"startIndex": 8, "endIndex": 8}, False),
        ({"suggestedInsertionIds": ["unexpected"]}, False),
        ({"unknownSemanticField": {}}, False),
    ],
)
def test_import_location_fields_are_typed_not_semantic_exceptions(
    *, indexed_run: bool, fields: dict[str, Any], accepted: bool
) -> None:
    title = _paragraph([("Import\n", {})])
    target = title["paragraph"]["elements"][0] if indexed_run else title
    target.update(fields)
    document = {"tabs": [{"documentTab": {"body": {"content": [title]}}}]}
    result = verify_markdown_import(
        document, markdown_import_manifest("", title="Import", branded=False)
    )
    assert result.content_preserved is True
    assert result.structure_preserved is accepted


@pytest.mark.parametrize("extra_cell_field", [False, True])
def test_indexed_section_table_and_cell_paragraphs_preserve_semantic_checks(
    *, extra_cell_field: bool
) -> None:
    title = _paragraph([("Import\n", {})])
    title.update(startIndex=1, endIndex=8)
    title["paragraph"]["elements"][0].update(startIndex=1, endIndex=8)
    cell = _paragraph([("Body\n", {})])
    cell.update(startIndex=11, endIndex=16)
    cell["paragraph"]["elements"][0].update(startIndex=11, endIndex=16)
    if extra_cell_field:
        cell["unknownSemanticField"] = {}
    document = {
        "tabs": [
            {
                "documentTab": {
                    "body": {
                        "content": [
                            {"endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
                            title,
                            {
                                "startIndex": 8,
                                "endIndex": 17,
                                "table": {
                                    "tableRows": [{"tableCells": [{"content": [cell]}]}],
                                },
                            },
                        ]
                    }
                }
            }
        ],
    }
    result = verify_markdown_import(
        document,
        markdown_import_manifest("| Body |\n| --- |", title="Import", branded=False),
    )
    assert result.content_preserved is True
    assert result.structure_preserved is not extra_cell_field


def test_checklist_state_is_part_of_the_exact_structure_signature() -> None:
    manifest = markdown_import_manifest("- [ ] Task", title="Import", branded=False)

    def document(symbol: str) -> dict[str, Any]:
        return {
            "tabs": [
                {
                    "childTabs": [],
                    "documentTab": {
                        "lists": {
                            "check": {
                                "listProperties": {"nestingLevels": [{"glyphSymbol": symbol}]}
                            }
                        },
                        "body": {
                            "content": [
                                _paragraph([("Import\n", {})]),
                                _paragraph(
                                    [("Task\n", {})],
                                    bullet={"listId": "check", "nestingLevel": 0},
                                ),
                            ]
                        },
                    },
                }
            ]
        }

    assert verify_markdown_import(document("☐"), manifest).structure_preserved is True
    assert verify_markdown_import(document("☒"), manifest).structure_preserved is False


def test_brand_readback_rejects_logo_segment_and_role_corruption() -> None:
    manifest = markdown_import_manifest("Body", title="Import", branded=True)
    mutations = []

    wrong_logo = _branded_document()
    wrong_logo["tabs"][0]["documentTab"]["inlineObjects"]["logo-1"]["inlineObjectProperties"][
        "embeddedObject"
    ]["description"] = "Sarj logo"
    mutations.append(wrong_logo)

    wrong_source = _branded_document()
    wrong_source["tabs"][0]["documentTab"]["inlineObjects"]["logo-1"]["inlineObjectProperties"][
        "embeddedObject"
    ]["imageProperties"] = {"sourceUri": "https://evil.example/logo.png"}
    mutations.append(wrong_source)

    wrong_logo_size = _branded_document()
    wrong_logo_size["tabs"][0]["documentTab"]["inlineObjects"]["logo-1"]["inlineObjectProperties"][
        "embeddedObject"
    ]["size"]["width"]["magnitude"] = 80
    mutations.append(wrong_logo_size)

    extra_header = _branded_document()
    extra_header["tabs"][0]["documentTab"]["headers"]["header-1"]["content"].append(
        _paragraph([("Foreign", {})])
    )
    mutations.append(extra_header)

    extra_footer = _branded_document()
    extra_footer["tabs"][0]["documentTab"]["footers"]["footer-1"]["content"][0]["paragraph"][
        "elements"
    ].append({"textRun": {"content": "Foreign", "textStyle": {}}})
    mutations.append(extra_footer)

    reordered_footer = _branded_document()
    footer_elements = reordered_footer["tabs"][0]["documentTab"]["footers"]["footer-1"]["content"][
        0
    ]["paragraph"]["elements"]
    footer_elements.reverse()
    mutations.append(reordered_footer)

    extra_tab = _branded_document()
    extra_tab["tabs"].append(
        {
            "tabProperties": {"tabId": "extra"},
            "childTabs": [],
            "documentTab": {"body": {"content": []}},
        }
    )
    mutations.append(extra_tab)

    wrong_title = _branded_document()
    title_style = wrong_title["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"][
        "elements"
    ][0]["textRun"]["textStyle"]
    title_style["fontSize"]["magnitude"] = 8
    title_style["foregroundColor"] = _rgb("#FF0000")
    mutations.append(wrong_title)

    for document in mutations:
        assert verify_markdown_import(document, manifest).brand_applied is False


def test_brand_readback_does_not_treat_explicit_zero_or_invalid_font_as_inherited() -> None:
    tokens = CURRENT_BRAND_MANIFEST.document
    manifest = markdown_import_manifest("Body", title="Import", branded=True)

    explicit_zero = _branded_document()
    explicit_zero["tabs"][0]["documentTab"]["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": _brand_run_style(
                    size=tokens.body_size_pt,
                    color=CURRENT_BRAND_MANIFEST.colors.ink,
                ),
                "paragraphStyle": _brand_paragraph_style(),
            }
        ]
    }
    explicit_zero["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["paragraphStyle"][
        "spaceAbove"
    ]["magnitude"] = 0
    assert verify_markdown_import(explicit_zero, manifest).brand_applied is False

    invalid_font = _branded_document()
    invalid_font["tabs"][0]["documentTab"]["namedStyles"] = explicit_zero["tabs"][0]["documentTab"][
        "namedStyles"
    ]
    invalid_font["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"][0][
        "textRun"
    ]["textStyle"]["weightedFontFamily"] = {}
    assert verify_markdown_import(invalid_font, manifest).brand_applied is False


def test_code_font_is_allowed_only_at_the_exact_expected_offsets() -> None:
    manifest = markdown_import_manifest("`x` x", title="Import", branded=True)
    document = _branded_document()
    code_style = _brand_run_style(
        size=CURRENT_BRAND_MANIFEST.document.body_size_pt,
        color=CURRENT_BRAND_MANIFEST.colors.ink,
    )
    code_style["weightedFontFamily"] = {"fontFamily": "Courier New"}
    plain_style = _brand_run_style(
        size=CURRENT_BRAND_MANIFEST.document.body_size_pt,
        color=CURRENT_BRAND_MANIFEST.colors.ink,
    )
    body_runs = document["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"]
    body_runs[:] = [
        {"textRun": {"content": "x", "textStyle": code_style}},
        {"textRun": {"content": " ", "textStyle": plain_style}},
        {"textRun": {"content": "x\n", "textStyle": plain_style}},
    ]
    assert verify_markdown_import(document, manifest).brand_applied is True

    body_runs[-1]["textRun"]["textStyle"] = code_style
    assert verify_markdown_import(document, manifest).brand_applied is False


def test_brand_readback_allows_named_style_inheritance_and_complex_script_fallback() -> None:
    tokens = CURRENT_BRAND_MANIFEST.document
    document = _branded_document()
    document["tabs"][0]["documentTab"]["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": _brand_run_style(
                    size=tokens.body_size_pt,
                    color=CURRENT_BRAND_MANIFEST.colors.ink,
                ),
                "paragraphStyle": _brand_paragraph_style(),
            }
        ]
    }
    body_style = document["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"][
        0
    ]["textRun"]["textStyle"]
    body_style.pop("weightedFontFamily")
    manifest = markdown_import_manifest("Body", title="Import", branded=True)
    assert verify_markdown_import(document, manifest).brand_applied is True

    body_style["weightedFontFamily"] = {"fontFamily": tokens.complex_script_font_family}
    document["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"][0]["textRun"][
        "content"
    ] = "العربية\n"
    manifest = markdown_import_manifest("العربية", title="Import", branded=True)
    assert verify_markdown_import(document, manifest).brand_applied is True


def test_google_gateway_uses_markdown_manifest_for_post_import_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document: dict[str, Any] = {
        "revisionId": "revision-2",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab-1"},
                "childTabs": [],
                "documentTab": {
                    "body": {
                        "content": [
                            _paragraph([("Import\n", {})]),
                            _paragraph([("Body\n", {})]),
                        ]
                    }
                },
            }
        ],
    }
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _document_id: document)
    source = SourceSnapshot(
        title="Import",
        parent_folder_id=FileId("folder"),
        modified_time=None,
        revision_id=None,
        media_type="text/markdown",
        markdown="Body",
    )
    expected = parse_markdown("Body", title="Import")
    draft = OutputDraft(
        output=DocumentOutput(
            file_id=FileId("output"),
            name="Import",
            parent_folder_id=FileId("folder"),
        ),
        document_id=FileId("output"),
        tab_id=DocsTabId("tab-1"),
        revision_id=DocsRevisionId("revision-2"),
        source_sha256="source",
    )

    verification = gateway._verify_output_sync(
        draft,
        source,
        expected,
        verify_brand=False,
    )

    assert verification.content_preserved is True
    assert verification.structure_preserved is True
