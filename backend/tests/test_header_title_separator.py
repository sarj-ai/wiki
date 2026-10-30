from copy import deepcopy
import hashlib
import json

import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST, HEADER_TITLE_SEPARATOR
from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.google_gateway import GoogleDocumentGateway, verify_brand_application
from app.document_transform.header_provenance import HeaderIdentityProof
from app.document_transform.ir import parse_google_document
from app.document_transform.markdown_import import verify_markdown_import
from app.files.service import GoogleFilesService
from app.models.user import User
from tests.test_document_transform import _branded_google_document
from tests.test_document_transform_header_provenance import _capture, _snapshot
from tests.test_markdown_native_header_title import _titled_document


@pytest.mark.parametrize("separator", ["\t", "    \t", "\u00a0\u00a0\t", "\u2003\u2003\t"])
def test_new_native_header_rejects_missing_or_substituted_gutter(separator: str) -> None:
    document = _branded_google_document()
    paragraph = document["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0]["paragraph"]
    paragraph["elements"][0]["textRun"]["content"] = f"Decision{separator}"
    assert not verify_brand_application(document, parse_google_document(document, title="Decision"))


@pytest.mark.parametrize("separator", ["\t", "    \t", "\u00a0\u00a0\t", "\u2003\u2003\t"])
def test_new_import_header_rejects_missing_or_substituted_gutter(separator: str) -> None:
    document = _titled_document("Chosen Title", newline=False)
    # Omit optional indexes so this assertion tests the exact separator, not a stale range.
    element = document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"][0]
    element.pop("startIndex")
    element.pop("endIndex")
    for run in element["paragraph"]["elements"]:
        run.pop("startIndex")
        run.pop("endIndex")
    element["paragraph"]["elements"][0]["textRun"]["content"] = f"Chosen Title{separator}"
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    result = verify_markdown_import(document, manifest)
    assert not result.brand_applied
    assert result.content_preserved


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("tampered", [False, True])
async def test_owned_header_separator_version_preserves_repeat_and_rejects_edit(
    monkeypatch: pytest.MonkeyPatch, *, legacy: bool, tampered: bool
) -> None:
    document, evidence = await _capture(monkeypatch)
    assert evidence.version == 2
    tab = document["tabs"][0]["documentTab"]
    header = tab["headers"]["header-a"]
    run = header["content"][0]["paragraph"]["elements"][0]["textRun"]
    if legacy:
        run["content"] = "Decision\t"
        logo = deepcopy(tab["inlineObjects"]["logo-1"])
        logo["inlineObjectProperties"]["embeddedObject"]["imageProperties"].pop("contentUri", None)
        # Reconstruct the historical v1 exact fixture fingerprint, independently of the verifier.
        fingerprint = hashlib.sha256(
            json.dumps(
                {"header": header, "logo": logo},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        evidence = evidence.model_copy(
            update={
                "version": 1,
                "headers": (
                    evidence.headers[0].model_copy(update={"fingerprint_sha256": fingerprint}),
                ),
            }
        )
    if tampered:
        run["content"] = "Decision" + (HEADER_TITLE_SEPARATOR if legacy else "\t")
    before = deepcopy(document)
    gateway = GoogleDocumentGateway(
        user=User(),
        files=object.__new__(GoogleFilesService),
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
        header_identity=HeaderIdentityProof(
            evidence=evidence, result_sha256="a" * 64, verified_revision_sha256="b" * 64
        ),
    )
    snapshot = _snapshot(document)
    if tampered:
        with pytest.raises(InPlaceNeedsReviewError):
            await gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
    else:
        inspection = await gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
        assert inspection.fully_branded
        assert inspection.headers == ()
        assert len(inspection.preserved_headers) == 1
        verification = await gateway.verify_in_place(
            document=document,
            title=snapshot.title,
            expected=parse_google_document(document, title=snapshot.title),
            structural_sha256=inspection.structural_sha256,
            protected_structure_sha256=inspection.protected_structure_sha256,
            verify_brand=True,
        )
        assert verification.brand_applied
        assert verification.content_preserved
        assert verification.structure_preserved
    assert document == before
