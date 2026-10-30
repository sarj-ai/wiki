from typing import assert_type

from sqlalchemy import Text

from app.database import Base
from app.identifiers import DriveId, FileId, GoogleUserId


def test_identifier_brands_are_zero_cost_at_runtime() -> None:
    raw_file_id = "opaque-google-file-id"
    raw_drive_id = "opaque-google-drive-id"
    raw_user_id = "opaque-google-user-id"

    file_id = FileId(raw_file_id)
    drive_id = DriveId(raw_drive_id)
    google_user_id = GoogleUserId(raw_user_id)

    assert file_id is raw_file_id
    assert drive_id is raw_drive_id
    assert google_user_id is raw_user_id
    assert_type(file_id, FileId)
    assert_type(drive_id, DriveId)
    assert_type(google_user_id, GoogleUserId)


def test_sqlalchemy_maps_identifier_brands_to_text() -> None:
    assert isinstance(Base.registry.type_annotation_map[FileId], Text)
    assert isinstance(Base.registry.type_annotation_map[DriveId], Text)
    assert isinstance(Base.registry.type_annotation_map[GoogleUserId], Text)
