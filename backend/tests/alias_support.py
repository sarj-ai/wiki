from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, override

from app.aliases.service import AliasService


if TYPE_CHECKING:
    from app.aliases.schemas import DocumentAliasRead, DocumentAliasValue
    from app.identifiers import FileId


class StrictAliasService(AliasService, ABC):
    @override
    async def ensure_for_file(
        self, file_id: FileId, candidates: tuple[DocumentAliasValue, ...]
    ) -> DocumentAliasRead:
        raise NotImplementedError
