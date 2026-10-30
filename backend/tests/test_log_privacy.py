from __future__ import annotations

from loguru import logger

from app.aliases.router import resolve_alias, retire_file_alias, save_file_alias
from app.aliases.schemas import (
    DocumentAliasForFile,
    DocumentAliasRead,
    DocumentAliasValue,
    DocumentAliasWrite,
)
from app.identifiers import FileId
from tests.alias_support import StrictAliasService


class _Aliases(StrictAliasService):
    async def get_for_file(self, file_id: FileId) -> DocumentAliasForFile:
        raise NotImplementedError

    async def save(self, file_id: FileId, alias: DocumentAliasValue) -> DocumentAliasRead:
        return DocumentAliasRead(alias=alias, file_id=file_id)

    async def retire(self, file_id: FileId) -> None:
        del file_id

    async def resolve(self, alias: DocumentAliasValue) -> DocumentAliasRead:
        return DocumentAliasRead(alias=alias, file_id=FileId("private-google-file-id"))


async def test_alias_audit_events_do_not_log_resource_identifiers() -> None:
    messages: list[str] = []
    sink = logger.add(messages.append, format="{message}")
    service = _Aliases()
    alias = DocumentAliasWrite.model_validate({"alias": "private-project-roadmap"}).alias
    file_id = FileId("private-google-file-id")
    try:
        await save_file_alias(file_id, DocumentAliasWrite(alias=alias), service)
        await retire_file_alias(file_id, service)
        await resolve_alias(alias, service)
    finally:
        logger.remove(sink)

    rendered = "".join(messages)
    assert "document alias saved" in rendered
    assert "document alias retired" in rendered
    assert "document alias resolved" in rendered
    assert alias not in rendered
    assert file_id not in rendered
