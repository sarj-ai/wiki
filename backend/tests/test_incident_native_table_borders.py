from copy import deepcopy
import json
from pathlib import Path

from pydantic import JsonValue
import pytest

from app.document_transform.readability import readability_invariant_sha256, verify_readability
from app.document_transform.readability_json import array, bodies, content, document_digest, obj
from app.document_transform.readability_tables import readable_table_requests
from app.document_transform.readability_types import (
    CURRENT_READABILITY_VERSION,
    LegacyReadabilityRefinement,
    PlannedReadabilityCheckpoint,
    ReadabilityTable,
)
from app.identifiers import DocsRevisionId


def _fixture(name: str) -> dict[str, JsonValue]:
    return json.loads(
        (Path(__file__).parent / "data" / "native_table_borders" / f"{name}.json").read_text()
    )


def _checkpoint(document: dict[str, JsonValue]) -> PlannedReadabilityCheckpoint:
    target = ReadabilityTable.model_validate(_fixture("original_target"))
    checkpoint = PlannedReadabilityCheckpoint(
        refinement=LegacyReadabilityRefinement(passes=0, model_invoked=False),
        version=CURRENT_READABILITY_VERSION,
        source_revision_id=DocsRevisionId(str(document["revisionId"])),
        source_sha256=document_digest(document),
        invariant_sha256="",
        tables=(target,),
        original_lists={str(target.tab_id): {}},
        requests=readable_table_requests(document, (target,)),
    )
    return checkpoint.model_copy(
        update={
            "invariant_sha256": readability_invariant_sha256(document, checkpoint, applied=False)
        }
    )


def test_captured_incident_table_omits_inherited_borders_without_weakening_native_verification() -> (
    None
):
    document = _fixture("original_readback")
    checkpoint = _checkpoint(document)
    assert all("border" not in str(request) for request in checkpoint.requests)
    assert verify_readability(document, checkpoint)


@pytest.mark.parametrize("corruption", ["text", "link", "border", "width", "header"])
def test_captured_incident_table_still_rejects_meaningful_native_changes(corruption: str) -> None:
    original = _fixture("original_readback")
    document = deepcopy(original)
    _, tab = next(bodies(document))
    table = obj(obj(content(tab)[0])["table"])
    row = obj(array(table["tableRows"])[0])
    cell = obj(array(row["tableCells"])[0])
    paragraph = obj(obj(array(cell["content"])[0])["paragraph"])
    run = obj(obj(array(paragraph["elements"])[0])["textRun"])
    match corruption:
        case "text":
            run["content"] = str(run["content"]).replace("X", "Y", 1)
        case "link":
            obj(run["textStyle"])["link"] = {"url": "https://example.test/changed"}
        case "border":
            obj(cell["tableCellStyle"])["borderTop"] = {"width": {"magnitude": 1, "unit": "PT"}}
        case "width":
            column = obj(array(obj(table["tableStyle"])["tableColumnProperties"])[0])
            obj(column["width"])["magnitude"] = 100
        case _:
            obj(row["tableRowStyle"])["tableHeader"] = False
    assert not verify_readability(document, _checkpoint(original))
