from pydantic import JsonValue
import pytest

from app.identifiers import FileId


async def unexpected_rollback(
    _document_id: FileId,
    _before: dict[str, JsonValue],
    _observed: dict[str, JsonValue],
) -> None:
    pytest.fail("A verified fixture write must not require rollback")
