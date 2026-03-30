"""Background service for processing import jobs."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from googleapiclient.http import MediaFileUpload
from sqlalchemy import select

from app.drive.credentials import get_sa_drive_service
from app.drive.folders import ensure_folder
from app.imports.parsers import get_parser
from app.imports.parsers.base import ParsedNode
from app.models.import_job import ImportJob
from app.models.page import Page

log = logging.getLogger(__name__)


def _count_nodes(nodes: list[ParsedNode]) -> int:
    """Recursively count all nodes in the parsed tree."""
    total = 0
    for node in nodes:
        total += 1
        total += _count_nodes(node.children)
    return total


def _upload_doc_to_drive(title: str, content_path: Path, mime_type: str, folder_id: str) -> str:
    """Upload a file to Drive with conversion to Google Docs format.

    Cleans Notion HTML before upload for better conversion fidelity.
    Returns the created Google Drive file ID.
    """
    import tempfile

    upload_path = content_path

    # Pre-process HTML for better Google Docs conversion
    if mime_type == "text/html":
        from app.imports.parsers.html_cleaner import clean_notion_html

        raw = content_path.read_text(encoding="utf-8", errors="replace")
        cleaned = clean_notion_html(raw)
        # Write cleaned HTML to a temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
        tmp.write(cleaned)
        tmp.close()
        upload_path = Path(tmp.name)

    try:
        service = get_sa_drive_service()
        doc = service.files().create(
            body={
                "name": title,
                "mimeType": "application/vnd.google-apps.document",
                "parents": [folder_id],
            },
            media_body=MediaFileUpload(str(upload_path), mimetype=mime_type),
            supportsAllDrives=True,
            fields="id",
        ).execute()
        return doc["id"]
    finally:
        # Clean up temp file if we created one
        if upload_path != content_path:
            upload_path.unlink(missing_ok=True)


def _grant_user_editor(doc_id: str, user_email: str) -> None:
    """Grant editor access to the importing user on a Drive file."""
    service = get_sa_drive_service()
    try:
        service.permissions().create(
            fileId=doc_id,
            body={
                "type": "user",
                "role": "writer",
                "emailAddress": user_email,
            },
            supportsAllDrives=True,
            sendNotificationEmail=False,
        ).execute()
    except Exception:
        log.warning("Failed to grant permission on %s to %s", doc_id, user_email)


def _create_drive_folder(name: str, parent_folder_id: str) -> str:
    """Create or find a folder in Drive. Returns the folder ID."""
    service = get_sa_drive_service()
    return ensure_folder(service, name, parent_folder_id)


async def process_import(
    job_id: UUID,
    extract_dir: Path,
    format_id: str,
    parent_id: UUID | None,
    workspace_id: UUID | None,
    user_email: str,
) -> None:
    """Background task: process an import job.

    Parses the extracted zip, creates Drive folders/docs, and inserts
    Page records into the database.
    """
    from app.database import async_session

    try:
        # Mark job as processing
        async with async_session() as db:
            result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
            job = result.scalar_one_or_none()
            if job is None:
                log.error("Import job %s not found", job_id)
                return
            job.status = "processing"
            await db.commit()

        # Parse the extracted directory
        parser = get_parser(format_id)
        tree = parser.parse(extract_dir)
        total = _count_nodes(tree)

        if total == 0:
            async with async_session() as db:
                result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = "No pages found in the uploaded file. Make sure you exported as HTML from Notion."
                    await db.commit()
            return
        async with async_session() as db:
            result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
            job = result.scalar_one_or_none()
            if job is None:
                return
            job.total_items = total
            await db.commit()

        # Determine the root Drive folder and page attributes from the parent
        root_folder_id: str | None = None
        is_private = False
        created_by: UUID | None = None

        if parent_id is not None:
            async with async_session() as db:
                result = await db.execute(select(Page).where(Page.id == parent_id))
                parent_page = result.scalar_one_or_none()
                if parent_page:
                    root_folder_id = parent_page.children_folder_id or parent_page.drive_folder_id
                    # Inherit workspace and privacy from parent
                    if workspace_id is None:
                        workspace_id = parent_page.workspace_id
                    is_private = parent_page.is_private
                    created_by = parent_page.created_by

        if root_folder_id is None and workspace_id is not None:
            from app.models.workspace import Workspace
            async with async_session() as db:
                result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
                workspace = result.scalar_one_or_none()
                if workspace:
                    root_folder_id = workspace.drive_folder_id

        # Resolve the importing user's ID for ownership
        if created_by is None:
            from app.models.user import User as UserModel
            async with async_session() as db:
                result = await db.execute(select(UserModel).where(UserModel.email == user_email))
                importing_user = result.scalar_one_or_none()
                if importing_user:
                    created_by = importing_user.id

        if root_folder_id is None:
            from app.config import settings
            from app.drive import ensure_folder
            root_folder_id = settings.DRIVE_ROOT_FOLDER_ID
            is_private = True
            # Put private imports in the user's drafts folder
            service = get_sa_drive_service()
            private_root = await asyncio.to_thread(ensure_folder, service, "My Drafts", root_folder_id)
            root_folder_id = await asyncio.to_thread(ensure_folder, service, user_email, private_root)

        # Walk the tree depth-first and create pages
        processed = 0

        async def _walk(
            nodes: list[ParsedNode],
            db_parent_id: UUID | None,
            drive_folder_id: str,
            sort_start: float,
        ) -> int:
            nonlocal processed
            sort_order = sort_start

            for node in nodes:
                # Skip if a page with the same title already exists under this parent
                async with async_session() as db:
                    dup = await db.execute(
                        select(Page).where(
                            Page.title == node.title,
                            Page.parent_id == db_parent_id,
                            Page.workspace_id == workspace_id,
                        )
                    )
                    if dup.scalar_one_or_none() is not None:
                        processed += 1
                        log.info("Import: skipping duplicate '%s'", node.title)
                        # Still need to update progress
                        async with async_session() as pdb:
                            result = await pdb.execute(select(ImportJob).where(ImportJob.id == job_id))
                            j = result.scalar_one_or_none()
                            if j:
                                j.processed_items = processed
                                await pdb.commit()
                        continue

                page_id = uuid4()

                if node.type == "section":
                    # Create a Drive folder for this section
                    section_folder_id = await asyncio.to_thread(
                        _create_drive_folder, node.title, drive_folder_id,
                    )
                    google_drive_id = f"section-{section_folder_id}"

                    # Create the section Page record
                    async with async_session() as db:
                        page = Page(
                            id=page_id,
                            parent_id=db_parent_id,
                            google_drive_id=google_drive_id,
                            drive_folder_id=drive_folder_id,
                            children_folder_id=section_folder_id,
                            title=node.title,
                            type="section",
                            sort_order=sort_order,
                            is_private=is_private,
                            created_by=created_by,
                            workspace_id=workspace_id,
                        )
                        db.add(page)
                        await db.commit()

                    processed += 1
                    sort_order += 1

                    # Update progress
                    async with async_session() as db:
                        result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
                        job = result.scalar_one_or_none()
                        if job:
                            job.processed_items = processed
                            await db.commit()

                    # Recurse into children
                    sort_order = await _walk(
                        node.children, page_id, section_folder_id, 0,
                    )

                else:
                    # Upload content file to Drive
                    if node.content_path is None:
                        continue

                    doc_id = await asyncio.to_thread(
                        _upload_doc_to_drive,
                        node.title,
                        node.content_path,
                        node.mime_type,
                        drive_folder_id,
                    )

                    # Grant the importing user editor access
                    await asyncio.to_thread(_grant_user_editor, doc_id, user_email)

                    # Create the page record
                    async with async_session() as db:
                        page = Page(
                            id=page_id,
                            parent_id=db_parent_id,
                            google_drive_id=doc_id,
                            drive_folder_id=drive_folder_id,
                            title=node.title,
                            type="page",
                            sort_order=sort_order,
                            is_private=is_private,
                            created_by=created_by,
                            workspace_id=workspace_id,
                        )
                        db.add(page)
                        await db.commit()

                    processed += 1
                    sort_order += 1

                    # Update progress
                    async with async_session() as db:
                        result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
                        job = result.scalar_one_or_none()
                        if job:
                            job.processed_items = processed
                            await db.commit()

                    # Rate-limit between Drive uploads
                    await asyncio.sleep(0.1)

            return sort_order

        await _walk(tree, parent_id, root_folder_id, 0)

        # Mark job completed
        async with async_session() as db:
            result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()

        log.info("Import job %s completed: %d items", job_id, processed)

    except Exception as exc:
        log.exception("Import job %s failed", job_id)
        try:
            async with async_session() as db:
                result = await db.execute(select(ImportJob).where(ImportJob.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = str(exc)[:1000]
                    await db.commit()
        except Exception:
            log.exception("Failed to mark import job %s as failed", job_id)
    finally:
        # Clean up temp directory
        try:
            # extract_dir might be a subdirectory of the temp dir; walk up to find the tempdir root
            temp_root = extract_dir
            while temp_root.parent != temp_root and not temp_root.name.startswith("sarj-import-"):
                temp_root = temp_root.parent
            if temp_root.name.startswith("sarj-import-"):
                shutil.rmtree(temp_root, ignore_errors=True)
            else:
                shutil.rmtree(extract_dir, ignore_errors=True)
        except Exception:
            log.warning("Failed to clean up temp dir %s", extract_dir)
