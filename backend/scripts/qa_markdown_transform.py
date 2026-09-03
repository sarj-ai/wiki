"""Create one explicitly confirmed native Google Doc from local Markdown for QA."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import uuid

import httpx
from sqlalchemy import select

from app.auth.google_tokens import GoogleTokenManager
from app.cache.dependencies import cache_service_dep
from app.config import settings
from app.database import async_session
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
from app.drive.service import GoogleDriveService
from app.files.service import GoogleFilesService
from app.identifiers import FileId
from app.models.user import User


_CONFIRMATION = "CREATE_LABELED_SIBLING"


def main() -> None:
    print(json.dumps(asyncio.run(_run(_arguments())), indent=2, sort_keys=True))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown-file", type=Path, required=True)
    parser.add_argument("--destination-folder-id", required=True)
    parser.add_argument("--document-name", required=True)
    parser.add_argument("--mode", choices=[item.value for item in TransformMode], required=True)
    parser.add_argument("--operation-id", type=uuid.UUID, required=True)
    parser.add_argument("--user-email", default="nasr@sarj.ai")
    parser.add_argument("--confirm-live-copy", required=True)
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    if arguments.confirm_live_copy != _CONFIRMATION:
        msg = f"--confirm-live-copy must equal {_CONFIRMATION}"
        raise ValueError(msg)
    project = settings.GOOGLE_CLOUD_PROJECT
    if project is None:
        msg = "GOOGLE_CLOUD_PROJECT is required"
        raise RuntimeError(msg)
    markdown = arguments.markdown_file.resolve().read_text(encoding="utf-8")
    async with async_session() as db, httpx.AsyncClient(timeout=30) as http:
        user = await db.scalar(select(User).where(User.email == arguments.user_email))
        if user is None:
            msg = "QA user is not present in the local database"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(db, http).prepare(user)
        drive = GoogleDriveService(
            prepared.user,
            cache_service_dep(),
            refresh_handler=prepared.refresh_handler,
        )
        files = GoogleFilesService(user=prepared.user, drive=drive)
        gateway = GoogleDocumentGateway(
            user=prepared.user,
            files=files,
            logo_uri="",
            refresh_handler=prepared.refresh_handler,
        )
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project=project,
                location="global",
                unslop_model=settings.DOCUMENT_TRANSFORM_UNSLOP_MODEL,
                fallback_model=settings.DOCUMENT_TRANSFORM_FALLBACK_MODEL,
                restyle_model=settings.DOCUMENT_TRANSFORM_RESTYLE_MODEL,
            )
        )
        logo_path = Path(__file__).parents[2] / "frontend/public/brand/sarj-logo-light.png"
        result = await DocumentTransformExecutor(
            gateway=gateway,
            generator=generator,
            logo_bytes=logo_path.read_bytes(),
        ).execute(
            operation_id=arguments.operation_id,
            request=DocumentTransformRequest(
                source=MarkdownSource(
                    kind="markdown",
                    content=markdown,
                    name=arguments.document_name,
                    destination_folder_id=FileId(arguments.destination_folder_id),
                ),
                mode=TransformMode(arguments.mode),
                output_name=arguments.document_name,
            ),
        )
        return result.model_dump(mode="json")


if __name__ == "__main__":
    main()
