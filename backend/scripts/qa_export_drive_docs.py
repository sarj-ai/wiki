"""Export explicitly named Google Docs to local PDFs for visual release QA."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from typing import Annotated

import httpx
from sqlalchemy import select
import typer

from app.auth.google_tokens import GoogleTokenManager
from app.database import async_session
from app.drive.retry import execute_with_retry
from app.drive.service import GoogleDriveService
from app.identifiers import FileId
from app.models.user import User


CLI = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
    help=__doc__,
)


@CLI.command()
def main(
    file_id: Annotated[list[str], typer.Option("--file-id")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    user_email: Annotated[str, typer.Option("--user-email")],
) -> None:
    print(
        json.dumps(
            asyncio.run(_run([FileId(value) for value in file_id], output_dir, user_email)),
            indent=2,
            sort_keys=True,
        )
    )


async def _run(file_ids: list[FileId], output_dir: Path, user_email: str) -> list[dict[str, str]]:
    output_dir = await asyncio.to_thread(Path.resolve, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    async with async_session() as db, httpx.AsyncClient(timeout=30) as http:
        if (user := await db.scalar(select(User).where(User.email == user_email))) is None:
            msg = "QA user is not present in the local database"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(db, http).prepare(user)
        client = GoogleDriveService.build_drive_client(
            prepared.user,
            prepared.refresh_handler,
        )
        exported: list[dict[str, str]] = []
        for file_id in file_ids:
            metadata = await asyncio.to_thread(
                execute_with_retry,
                client.files().get(fileId=file_id, fields="id,name,modifiedTime"),
            )
            if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
                msg = "Drive returned invalid file metadata"
                raise TypeError(msg)
            pdf = await asyncio.to_thread(
                execute_with_retry,
                client.files().export(fileId=file_id, mimeType="application/pdf"),
            )
            if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF"):
                msg = "Drive did not return a PDF export"
                raise RuntimeError(msg)
            path = output_dir / f"{_safe_name(metadata['name'])}-{file_id[:8]}.pdf"
            path.write_bytes(pdf)
            exported.append(
                {
                    "file_id": file_id,
                    "modified_time": str(metadata.get("modifiedTime", "")),
                    "name": metadata["name"],
                    "path": str(path),
                }
            )
        return exported


def _safe_name(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return compact[:100] or "document"


if __name__ == "__main__":
    CLI()
