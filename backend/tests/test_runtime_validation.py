from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.auth.schemas import GoogleTokenResponse, GoogleUserInfo
from app.drive.google_schemas import GoogleDrive, GoogleFile
from app.extras.schemas import DocIconWrite, SortOrderWrite
from app.files.schemas import FileCreate


def test_google_file_maps_camelcase_aliases() -> None:
    file = GoogleFile.model_validate(
        {
            "id": "f1",
            "name": "Plan",
            "mimeType": "application/vnd.google-apps.document",
            "driveId": "d1",
            "parents": ["d1"],
            "modifiedTime": "2026-01-01T00:00:00Z",
            "shortcutDetails": {"targetId": "t1", "targetMimeType": "application/pdf"},
        }
    )
    assert file.mime_type == "application/vnd.google-apps.document"
    assert file.drive_id == "d1"
    assert file.shortcut_details is not None
    assert file.shortcut_details.target_id == "t1"


def test_google_file_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        GoogleFile.model_validate({"id": "f1", "mimeType": "x"})


def test_google_drive_rejects_missing_name() -> None:
    with pytest.raises(ValidationError):
        GoogleDrive.model_validate({"id": "d1"})


def test_google_token_response_requires_access_token() -> None:
    with pytest.raises(ValidationError):
        GoogleTokenResponse.model_validate({"refresh_token": "r"})


def test_google_user_info_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        GoogleUserInfo.model_validate({"id": "g1", "email": "not-an-email", "verified_email": True})


@pytest.mark.parametrize("verified_email", [False, 1, "true"])
def test_google_user_info_requires_literal_verified_email(verified_email: object) -> None:
    with pytest.raises(ValidationError):
        GoogleUserInfo.model_validate(
            {"id": "g1", "email": "user@example.com", "verified_email": verified_email}
        )


def test_sort_order_rejects_non_finite() -> None:
    for bad in ("inf", "-inf", "nan"):
        with pytest.raises(ValidationError):
            SortOrderWrite.model_validate({"drive_id": "d1", "sort_order": float(bad)})


def test_doc_icon_rejects_non_url_cover() -> None:
    with pytest.raises(ValidationError):
        DocIconWrite.model_validate({"cover_url": "javascript:alert(1)"})


def test_doc_icon_accepts_https_cover() -> None:
    payload = DocIconWrite.model_validate({"icon": "📄", "cover_url": "https://example.com/c.png"})
    assert str(payload.cover_url) == "https://example.com/c.png"


def test_file_create_rejects_blank_and_oversized_name() -> None:
    with pytest.raises(ValidationError):
        FileCreate.model_validate({"parent_folder_id": "p", "name": "", "mime_type": "m"})
    with pytest.raises(ValidationError):
        FileCreate.model_validate({"parent_folder_id": "p", "name": "x" * 256, "mime_type": "m"})
