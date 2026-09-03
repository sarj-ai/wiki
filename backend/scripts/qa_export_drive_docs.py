"""Export explicitly named Google Docs to local PDFs for visual release QA."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re

import httpx
from sqlalchemy import select

from app.auth.google_tokens import GoogleTokenManager
from app.database import async_session
from app.drive.retry import execute_with_retry
from app.drive.service import GoogleDriveService
from app.models.user import User


def main() -> None:
    print(json.dumps(asyncio.run(_run(_arguments())), indent=2, sort_keys=True))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-id", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--user-email", default="nasr@sarj.ai")
    return parser.parse_args()


async def _run(arguments: argparse.Namespace) -> list[dict[str, str]]:
    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    async with async_session() as db, httpx.AsyncClient(timeout=30) as http:
        user = await db.scalar(select(User).where(User.email == arguments.user_email))
        if user is None:
            msg = "QA user is not present in the local database"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(db, http).prepare(user)
        client = GoogleDriveService.build_drive_client(
            prepared.user,
            prepared.refresh_handler,
        )
        exported: list[dict[str, str]] = []
        for file_id in arguments.file_id:
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
    main()
