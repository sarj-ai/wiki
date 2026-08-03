"""Partial-unique-index semantics of document_aliases against real Postgres.

uq_document_aliases_active_file (unique on file_id WHERE retired_at IS NULL)
only exists in the real database — fake-session tests cannot exercise it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.identifiers import FileId
from app.models.wiki import DocumentAlias


pytestmark = pytest.mark.integration


def _alias(alias: str, file_id: str) -> DocumentAlias:
    row = DocumentAlias()
    row.alias = alias
    row.file_id = FileId(file_id)
    return row


async def test_a_file_can_hold_only_one_active_alias(pg_session: AsyncSession) -> None:
    pg_session.add(_alias("spec-notes", "file-a"))
    await pg_session.commit()

    pg_session.add(_alias("spec-again", "file-a"))
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()


async def test_retiring_an_alias_frees_the_file_for_reregistration(
    pg_session: AsyncSession,
) -> None:
    first = _alias("spec-notes", "file-a")
    pg_session.add(first)
    await pg_session.commit()

    first.retired_at = datetime.now(UTC)
    await pg_session.commit()

    pg_session.add(_alias("spec-again", "file-a"))
    await pg_session.commit()

    active = await pg_session.get(DocumentAlias, "spec-again")
    assert active is not None
    assert active.retired_at is None
