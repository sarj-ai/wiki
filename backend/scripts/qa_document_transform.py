"""Run one explicitly confirmed live Document Polish copy for release QA.

This script never edits the source. It uses the same gateway, safety gates, and
Vertex policy as the queued endpoint, creates a labeled sibling, and verifies
that the source Drive revision is unchanged before reporting success.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Annotated
import uuid

import httpx
from sqlalchemy import select
import typer

from app.auth.google_tokens import GoogleTokenManager
from app.config import settings
from app.database import async_session
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.qa_resources import prepare_qa_drive
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    TransformMode,
)
from app.drive.schemas import MIME_DOC
from app.files.service import GoogleFilesService
from app.identifiers import FileId
from app.models.user import User


_CONFIRMATION = "CREATE_LABELED_SIBLING"
CLI = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
    help=__doc__,
)


@dataclass(frozen=True, kw_only=True)
class _Arguments:
    source_id: str
    mode: TransformMode
    output_name: str
    user_email: str
    confirm_live_copy: str


@CLI.command()
def main(
    source_id: Annotated[str, typer.Option("--source-id")],
    mode: Annotated[TransformMode, typer.Option("--mode")],
    output_name: Annotated[str, typer.Option("--output-name")],
    confirm_live_copy: Annotated[str, typer.Option("--confirm-live-copy")],
    user_email: Annotated[str, typer.Option("--user-email")],
) -> None:
    arguments = _Arguments(
        source_id=source_id,
        mode=mode,
        output_name=output_name,
        user_email=user_email,
        confirm_live_copy=confirm_live_copy,
    )
    print(json.dumps(asyncio.run(_run(arguments)), indent=2, sort_keys=True))


async def _run(arguments: _Arguments) -> dict[str, object]:
    if arguments.confirm_live_copy != _CONFIRMATION:
        msg = f"--confirm-live-copy must equal {_CONFIRMATION}"
        raise ValueError(msg)
    project = settings.GOOGLE_CLOUD_PROJECT
    if project is None:
        msg = "GOOGLE_CLOUD_PROJECT is required"
        raise RuntimeError(msg)
    async with async_session() as db, httpx.AsyncClient(timeout=30) as http:
        if (
            user := await db.scalar(select(User).where(User.email == arguments.user_email))
        ) is None:
            msg = "QA user is not present in the local database"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(db, http).prepare(user)
        drive = await prepare_qa_drive(prepared)
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
                mode=arguments.mode,
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
    CLI()
