"""Run one explicitly confirmed live Document Polish copy for release QA.

This script never edits the source. It uses the same gateway, safety gates, and
Vertex policy as the queued endpoint, creates a labeled sibling, and verifies
that the source Drive revision is unchanged before reporting success.
"""

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
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    TransformMode,
)
from app.drive.schemas import MIME_DOC
from app.drive.service import GoogleDriveService
from app.files.service import GoogleFilesService
from app.identifiers import FileId
from app.models.user import User


_CONFIRMATION = "CREATE_LABELED_SIBLING"


def main() -> None:
    print(json.dumps(asyncio.run(_run(_arguments())), indent=2, sort_keys=True))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--mode", choices=[item.value for item in TransformMode], required=True)
    parser.add_argument("--output-name", required=True)
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
        source_id = FileId(arguments.source_id)
        before = await files.get(source_id)
        before_modified_time = before.modified_time
        if before_modified_time is None:
            msg = "Drive source did not provide modifiedTime"
            raise RuntimeError(msg)
        logo_uri = await _verified_logo_uri(http, before.mime_type, arguments.mode)
        gateway = GoogleDocumentGateway(
            user=prepared.user,
            files=files,
            logo_uri=logo_uri,
            refresh_handler=prepared.refresh_handler,
        )
        logo_bytes = None
        if before.mime_type != MIME_DOC and arguments.mode != TransformMode.UNSLOP:
            logo_path = Path(__file__).parents[2] / "frontend/public/brand/sarj-logo-light.png"
            logo_bytes = logo_path.read_bytes()
        result = await DocumentTransformExecutor(
            gateway=gateway,
            generator=VertexGeminiPatchGenerator(
                policy=GeminiModelPolicy(
                    project=project,
                    location="global",
                    unslop_model=settings.DOCUMENT_TRANSFORM_UNSLOP_MODEL,
                    fallback_model=settings.DOCUMENT_TRANSFORM_FALLBACK_MODEL,
                    restyle_model=settings.DOCUMENT_TRANSFORM_RESTYLE_MODEL,
                )
            ),
            logo_bytes=logo_bytes,
        ).execute(
            operation_id=uuid.uuid7(),
            request=DocumentTransformRequest(
                source=DriveFileSource(
                    kind="drive_file",
                    file_id=source_id,
                    expected_modified_time=before_modified_time,
                ),
                mode=TransformMode(arguments.mode),
                output_name=arguments.output_name,
            ),
        )
        after = await files.get(source_id)
        if after.modified_time != before_modified_time:
            msg = "Source modifiedTime changed during QA"
            raise RuntimeError(msg)
        return {
            "source": {
                "file_id": source_id,
                "modified_time_before": before_modified_time,
                "modified_time_after": after.modified_time,
                "unchanged": True,
            },
            "result": result.model_dump(mode="json"),
        }


async def _verified_logo_uri(
    http: httpx.AsyncClient,
    mime_type: str,
    mode: str,
) -> str:
    logo_uri = str(settings.DOCUMENT_TRANSFORM_LOGO_URI)
    if mime_type != MIME_DOC or mode == TransformMode.UNSLOP:
        return logo_uri
    response = await http.get(logo_uri)
    if response.status_code == httpx.codes.OK and response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        return logo_uri
    return ""


if __name__ == "__main__":
    main()
