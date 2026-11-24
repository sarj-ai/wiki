from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.errors import OutputVerificationError
from app.document_transform.postprocessing_recovery import restore_verified_document
from app.identifiers import FileId


async def test_rollback_cannot_redefine_the_verified_preimage() -> None:
    before: dict[str, JsonValue] = {
        "documentId": "fixture",
        "revisionId": "verified",
        "title": "Verified content",
        "tabs": [],
    }
    original = deepcopy(before)
    document = {**before, "revisionId": "rejected", "title": "Rejected content"}

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def incomplete_rollback(
        _identifier: FileId,
        supplied_before: dict[str, JsonValue],
        _observed: dict[str, JsonValue],
    ) -> None:
        supplied_before["title"] = "Rejected content"
        document.clear()
        document.update(supplied_before)
        document["revisionId"] = "restored"

    with pytest.raises(OutputVerificationError):
        await restore_verified_document(
            document_id=FileId("fixture"),
            before=before,
            observed=deepcopy(document),
            read=read,
            rollback=incomplete_rollback,
        )

    assert before == original
