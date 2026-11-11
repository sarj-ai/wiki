from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue
import pytest

from app.document_transform.errors import OutputVerificationError
from app.document_transform.readability import verify_readability
from app.document_transform.readability_json import array, bodies, obj
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_types import ReadabilityCheckpoint
from app.identifiers import FileId
from tests.test_document_readability import _fixture, _paragraph, _plan


@dataclass(frozen=True, slots=True)
class _MarkerStyles:
    paragraph: dict[str, JsonValue]
    definition: dict[str, JsonValue]


def _marker_styles(
    document: dict[str, JsonValue], *, role: Literal["bullet", "numbered"]
) -> _MarkerStyles:
    start = 19 if role == "bullet" else 91
    bullet = obj(obj(_paragraph(document, start)["paragraph"])["bullet"])
    tab = next(bodies(document))[1]
    definition = obj(obj(tab["lists"])[str(bullet["listId"])])
    marker = obj(array(obj(definition["listProperties"])["nestingLevels"])[0])
    return _MarkerStyles(
        obj(bullet.setdefault("textStyle", {})), obj(marker.setdefault("textStyle", {}))
    )


@pytest.mark.parametrize("role", ["bullet", "numbered"])
@pytest.mark.parametrize("layer", ["paragraph", "list"])
@pytest.mark.parametrize(
    "mutation",
    [
        {"fontSize": {"magnitude": 1, "unit": "PT"}},
        {"foregroundColor": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}}},
        {"weightedFontFamily": {"fontFamily": "Arial"}},
        {"weightedFontFamily": {"weight": 900}},
        {"bold": True},
        {"strikethrough": True},
        {"baselineOffset": "SUPERSCRIPT"},
        {"link": {"url": "https://example.test/unplanned"}},
    ],
    ids=["tiny", "invisible", "font", "weight", "bold", "strike", "superscript", "link"],
)
async def test_readback_rejects_unplanned_list_marker_typography(
    role: Literal["bullet", "numbered"],
    layer: Literal["paragraph", "list"],
    mutation: dict[str, JsonValue],
) -> None:
    plan = await _plan()
    after = _fixture("after")
    styles = _marker_styles(after, role=role)
    if layer == "list":
        for key in mutation:
            styles.paragraph.pop(key, None)
    (styles.paragraph if layer == "paragraph" else styles.definition).update(mutation)

    assert not verify_readability(after, plan)


@pytest.mark.parametrize("role", ["bullet", "numbered"])
async def test_marker_typography_resolves_named_list_and_paragraph_inheritance(
    role: Literal["bullet", "numbered"],
) -> None:
    plan = await _plan()
    after = _fixture("after")
    styles = _marker_styles(after, role=role)
    styles.definition.update(deepcopy(styles.paragraph))
    styles.paragraph.clear()
    styles.paragraph["weightedFontFamily"] = {"fontFamily": "Nunito"}
    obj(styles.definition["weightedFontFamily"]).pop("fontFamily", None)

    assert verify_readability(after, plan)
    styles.definition["weightedFontFamily"] = {"fontFamily": "Arial", "weight": 400}
    assert verify_readability(after, plan)
    obj(styles.definition["weightedFontFamily"])["weight"] = 900
    assert not verify_readability(after, plan)


async def test_provider_rejects_a_completed_write_with_unreadable_markers() -> None:
    plan = await _plan()
    document = _fixture("before")

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_identifier: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        document.clear()
        document.update(_fixture("after"))
        styles = _marker_styles(document, role="numbered")
        styles.paragraph["fontSize"] = {"magnitude": 1, "unit": "PT"}

    with pytest.raises(OutputVerificationError):
        await ReadabilityProvider(read, write).apply(document_id=FileId("fixture"), checkpoint=plan)
