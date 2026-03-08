"""Google Drive helpers — create docs, folders, move files."""

from __future__ import annotations

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.auth.encryption import decrypt_token
from app.config import settings
from app.models.user import User


def _get_drive_service(user: User):
    """Build an authenticated Drive service using the user's stored tokens.

    Provides refresh_token + client credentials so the Google client library
    can transparently refresh an expired access token.
    """
    access_token = decrypt_token(user.encrypted_access_token)
    refresh_token = (
        decrypt_token(user.encrypted_refresh_token)
        if user.encrypted_refresh_token
        else None
    )
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )
    return build("drive", "v3", credentials=creds, static_discovery=True)


def _ensure_folder(service, name: str, parent_id: str) -> str:
    """Find or create a folder with the given name under parent_id.

    Returns the folder's Drive ID.
    """
    query = (
        f"name = '{name}' "
        f"and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = service.files().list(
        q=query, fields="files(id)", spaces="drive"
    ).execute()
    files = results.get("files", [])

    if files:
        return files[0]["id"]

    # Create the folder
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def create_folder(user: User, name: str, parent_folder_id: str) -> str:
    """Create a new folder in Drive. Always creates (no dedup by name).

    Returns the new folder's Drive ID.
    """
    service = _get_drive_service(user)
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def create_google_doc(
    user: User,
    title: str,
    *,
    is_private: bool,
    target_folder_id: str | None = None,
) -> tuple[str, str]:
    """Create a Google Doc in the correct Drive folder.

    If ``target_folder_id`` is given the doc is placed there directly
    (used when nesting under a parent page).

    Otherwise the root logic applies:
    - Private pages  → Sarj Docs/Private/{email}/
    - Workspace pages → Sarj Docs/ root (workspace folders come later).

    Returns (google_drive_id, drive_folder_id).
    """
    service = _get_drive_service(user)

    if target_folder_id is not None:
        folder_id = target_folder_id
    else:
        root_id = settings.DRIVE_ROOT_FOLDER_ID
        if is_private:
            private_root = _ensure_folder(service, "Private", root_id)
            user_folder = _ensure_folder(service, user.email, private_root)
            folder_id = user_folder
        else:
            folder_id = root_id

    # Create the Google Doc
    doc_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
    }
    doc = service.files().create(body=doc_metadata, fields="id").execute()

    return doc["id"], folder_id


def rename_drive_file(user: User, file_id: str, new_name: str) -> None:
    """Rename a Drive file."""
    service = _get_drive_service(user)
    service.files().update(fileId=file_id, body={"name": new_name}).execute()


def rename_child_folder(user: User, old_name: str, new_name: str, parent_folder_id: str) -> None:
    """Rename a page's children folder in Drive (if it exists).

    When a parent page is renamed, its children folder should match.
    """
    service = _get_drive_service(user)
    query = (
        f"name = '{old_name}' "
        f"and '{parent_folder_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = service.files().list(q=query, fields="files(id)", spaces="drive").execute()
    files = results.get("files", [])
    if files:
        service.files().update(fileId=files[0]["id"], body={"name": new_name}).execute()


def move_drive_file(user: User, file_id: str, new_parent_folder_id: str) -> None:
    """Move a Drive file to a different folder."""
    service = _get_drive_service(user)
    # Get current parents
    file_meta = service.files().get(fileId=file_id, fields="parents").execute()
    old_parents = ",".join(file_meta.get("parents", []))
    service.files().update(
        fileId=file_id,
        addParents=new_parent_folder_id,
        removeParents=old_parents,
        fields="id,parents",
    ).execute()


def trash_drive_file(user: User, file_id: str) -> None:
    """Move a Drive file to the trash."""
    service = _get_drive_service(user)
    service.files().update(fileId=file_id, body={"trashed": True}).execute()


def is_drive_file_trashed(user: User, file_id: str) -> bool | None:
    """Check if a Drive file is trashed or deleted.

    Returns True if trashed, False if not, None if file doesn't exist.
    """
    service = _get_drive_service(user)
    try:
        meta = service.files().get(fileId=file_id, fields="trashed").execute()
        return meta.get("trashed", False)
    except Exception:
        return None  # file doesn't exist or not accessible


def batch_check_trashed(user: User, file_ids: list[str]) -> dict[str, bool]:
    """Check multiple Drive files for trashed status.

    Returns a dict of {file_id: is_trashed_or_deleted}.
    Files that can't be found are considered deleted (True).
    """
    service = _get_drive_service(user)
    results: dict[str, bool] = {}

    # Use batch requests for efficiency
    batch = service.new_batch_http_request()

    def make_callback(fid: str):
        def callback(request_id, response, exception):
            if exception:
                results[fid] = True  # can't access = treat as deleted
            else:
                results[fid] = response.get("trashed", False)
        return callback

    for fid in file_ids:
        batch.add(
            service.files().get(fileId=fid, fields="trashed"),
            callback=make_callback(fid),
        )

    batch.execute()
    return results


def get_doc_content(user: User, google_drive_id: str) -> dict:
    """Fetch a Google Doc's full content via the Docs API.

    Returns the raw document JSON from documents.get().
    """
    access_token = decrypt_token(user.encrypted_access_token)
    refresh_token = (
        decrypt_token(user.encrypted_refresh_token)
        if user.encrypted_refresh_token
        else None
    )
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )
    docs_service = build("docs", "v1", credentials=creds, static_discovery=True)
    return docs_service.documents().get(documentId=google_drive_id).execute()


def export_doc_html(user: User, google_drive_id: str) -> str:
    """Export a Google Doc as HTML via the Drive API.

    The HTML export renders smart chips (Person, Status, File, Date, etc.)
    as actual text — unlike the Docs API which returns opaque placeholders.
    """
    service = _get_drive_service(user)
    response = service.files().export(
        fileId=google_drive_id,
        mimeType="text/html",
    ).execute()
    if isinstance(response, bytes):
        return response.decode("utf-8")
    return response
