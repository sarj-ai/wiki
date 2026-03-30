"""Import endpoints: list formats, upload zip, check status."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.imports.schemas import ImportJobResponse
from app.models.import_job import ImportJob
from app.models.user import User
from app.models.workspace import WorkspaceMembership

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/formats")
async def list_import_formats(user: User = Depends(get_current_user)) -> list[dict]:
    """Return all registered import formats."""
    from app.imports.parsers import list_formats

    return list_formats()


@router.post("/upload", response_model=ImportJobResponse, status_code=201)
async def upload_import(
    background_tasks: BackgroundTasks,
    file: UploadFile = ...,
    format: str = Form(...),
    parent_id: UUID | None = Form(None),
    workspace_id: UUID | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImportJobResponse:
    """Upload a zip file to import and start background processing."""
    # Validate format exists
    from app.imports.parsers import get_parser

    try:
        get_parser(format)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown import format: {format}",
        )

    # Validate workspace membership if workspace_id provided
    if workspace_id is not None:
        result = await db.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.workspace_id == workspace_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a member of the target workspace",
            )

    # Validate parent page if provided
    if parent_id is not None:
        from app.models.page import Page

        result = await db.execute(select(Page).where(Page.id == parent_id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent page not found",
            )

    # Save uploaded file to temp dir
    extract_dir = Path(tempfile.mkdtemp(prefix="sarj-import-"))
    tmp_path = extract_dir / "upload.zip"
    try:
        content = await file.read()

        if len(content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty",
            )

        tmp_path.write_bytes(content)

        # Validate it's a zip file
        if not zipfile.is_zipfile(tmp_path):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is not a valid zip archive",
            )

        # Extract the zip
        with zipfile.ZipFile(tmp_path) as zf:
            zf.extractall(extract_dir)

        # Remove the upload zip after extraction
        tmp_path.unlink(missing_ok=True)

        # Notion sometimes wraps the export in a nested zip — extract inner zips
        for inner_zip in list(extract_dir.rglob("*.zip")):
            with zipfile.ZipFile(inner_zip) as izf:
                izf.extractall(inner_zip.parent)
            inner_zip.unlink()

        # Notion zips often have a single top-level folder — unwrap it
        actual_dir = extract_dir
        subdirs = [d for d in extract_dir.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != "__MACOSX"]
        has_content = any(extract_dir.glob("*.html")) or any(extract_dir.glob("*.md"))
        if len(subdirs) == 1 and not has_content:
            actual_dir = subdirs[0]
            # Check one more level — Notion can double-nest
            inner_subdirs = [d for d in actual_dir.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != "__MACOSX"]
            inner_has_content = any(actual_dir.glob("*.html")) or any(actual_dir.glob("*.md"))
            if len(inner_subdirs) == 1 and not inner_has_content:
                actual_dir = inner_subdirs[0]

    except HTTPException:
        raise
    except Exception as exc:
        import shutil

        shutil.rmtree(extract_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to extract zip file: {exc}",
        )

    # Create ImportJob record
    job = ImportJob(
        user_id=user.id,
        workspace_id=workspace_id,
        parent_id=parent_id,
        format=format,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Schedule background processing
    from app.imports.service import process_import

    background_tasks.add_task(
        process_import,
        job_id=job.id,
        extract_dir=actual_dir,
        format_id=format,
        parent_id=parent_id,
        workspace_id=workspace_id,
        user_email=user.email,
    )

    return ImportJobResponse.model_validate(job)


@router.get("/{job_id}/status", response_model=ImportJobResponse)
async def get_import_status(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImportJobResponse:
    """Get the current status of an import job."""
    result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found",
        )

    # Only the user who created the job can check its status
    if job.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this import job",
        )

    return ImportJobResponse.model_validate(job)
