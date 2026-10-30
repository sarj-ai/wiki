from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self
import uuid

from google.auth.credentials import TokenState
import pytest

from app.document_transform import gcs_uploads
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.document_transform.schemas import MarkdownSource
from app.identifiers import FileId


@dataclass
class FakeBlob:
    data: bytes = b"# Hello\n"
    generation: str = "19"
    size: int = 8
    sign_kwargs: dict[str, object] | None = None
    download_calls: list[dict[str, object]] = field(default_factory=list)

    def generate_signed_url(self, **kwargs: object) -> str:
        self.sign_kwargs = kwargs
        return "https://storage.googleapis.com/bucket/object?signature"

    def reload(self) -> None:
        pass

    def download_as_bytes(self, **kwargs: object) -> bytes:
        self.download_calls.append(kwargs)
        return self.data


@dataclass
class FakeClient:
    blob_value: FakeBlob
    bucket_name: str | None = None
    blob_name: str | None = None

    def bucket(self, name: str) -> Self:
        self.bucket_name = name
        return self

    def blob(self, name: str) -> FakeBlob:
        self.blob_name = name
        return self.blob_value


class FakeCredentials:
    token = "access-token"
    token_state = TokenState.INVALID
    refresh_count = 0

    def refresh(self, _request: object) -> None:
        self.refresh_count += 1
        self.token_state = TokenState.FRESH


def _source() -> MarkdownSource:
    return MarkdownSource(
        kind="uploaded_markdown",
        upload_id=uuid.UUID("018fe109-a478-7ae8-9100-e80423cd0001"),
        name="Import",
        destination_folder_id=FileId("folder"),
    )


def test_signing_binds_upload_to_owner_and_limits_size(monkeypatch: pytest.MonkeyPatch) -> None:
    credentials = FakeCredentials()
    gcs_uploads._signing_credentials.cache_clear()
    monkeypatch.setattr(gcs_uploads.google.auth, "default", lambda **_: (credentials, None))
    blob = FakeBlob()
    client = FakeClient(blob)
    monkeypatch.setattr(gcs_uploads, "_client", lambda: client)
    owner = uuid.UUID("018fe109-a478-7ae8-9100-e80423cd0002")
    upload_id = _source().upload_id
    assert upload_id is not None

    result = gcs_uploads._sign("bucket", "backend@example.com", owner, upload_id)

    assert client.bucket_name == "bucket"
    assert client.blob_name == f"markdown/{owner}/{upload_id}"
    assert result.headers == {
        "Content-Type": "text/markdown",
        "x-goog-content-length-range": f"1,{MAX_MARKDOWN_BYTES}",
        "x-goog-if-generation-match": "0",
    }
    assert blob.sign_kwargs is not None
    assert blob.sign_kwargs["method"] == "PUT"
    monkeypatch.setattr(
        gcs_uploads.google.auth,
        "default",
        lambda **_: pytest.fail("ADC discovery must be cached across uploads"),
    )
    gcs_uploads._sign("bucket", "backend@example.com", owner, uuid.uuid7())
    assert credentials.refresh_count == 1
    credentials.token_state = TokenState.STALE
    gcs_uploads._sign("bucket", "backend@example.com", owner, uuid.uuid7())
    assert credentials.refresh_count == 2
    gcs_uploads._signing_credentials.cache_clear()


def test_download_is_generation_bound_and_utf8_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = FakeBlob()
    client = FakeClient(blob)
    monkeypatch.setattr(gcs_uploads, "_client", lambda: client)

    markdown = gcs_uploads._load("bucket", uuid.UUID(int=42), _source())

    assert markdown.content == "# Hello\n"
    assert blob.download_calls == [{"if_generation_match": 19, "raw_download": True}]
    blob.data = b"\xff"
    with pytest.raises(UnsupportedSourceError):
        gcs_uploads._load("bucket", uuid.UUID(int=42), _source())
    blob.size = MAX_MARKDOWN_BYTES + 1
    with pytest.raises(SourceTooLargeError):
        gcs_uploads._load("bucket", uuid.UUID(int=42), _source())
    assert len(blob.download_calls) == 2
