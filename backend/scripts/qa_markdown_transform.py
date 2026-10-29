"""Create one explicitly confirmed native Google Doc from local Markdown for QA."""

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
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
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
    markdown_file: Path
    destination_folder_id: str
    document_name: str
    mode: TransformMode
    operation_id: uuid.UUID
    user_email: str
    confirm_live_copy: str


@CLI.command()
def main(
    *,
    markdown_file: Annotated[Path, typer.Option("--markdown-file")],
    destination_folder_id: Annotated[str, typer.Option("--destination-folder-id")],
    document_name: Annotated[str, typer.Option("--document-name")],
    mode: Annotated[TransformMode, typer.Option("--mode")],
    operation_id: Annotated[uuid.UUID, typer.Option("--operation-id")],
    confirm_live_copy: Annotated[str, typer.Option("--confirm-live-copy")],
    user_email: Annotated[str, typer.Option("--user-email")],
) -> None:
    arguments = _Arguments(
        markdown_file=markdown_file,
        destination_folder_id=destination_folder_id,
        document_name=document_name,
        mode=mode,
        operation_id=operation_id,
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
    markdown = arguments.markdown_file.resolve().read_text(encoding="utf-8")
    async with async_session() as db, httpx.AsyncClient(timeout=30) as http:
        if (
            user := await db.scalar(select(User).where(User.email == arguments.user_email))
        ) is None:
            msg = "QA user is not present in the local database"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(db, http).prepare(user)
        drive = await prepare_qa_drive(prepared)
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
                mode=arguments.mode,
                output_name=arguments.document_name,
            ),
        )
        return result.model_dump(mode="json")


if __name__ == "__main__":
    CLI()
