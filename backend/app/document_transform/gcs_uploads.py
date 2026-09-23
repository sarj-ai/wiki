from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from importlib import import_module
import secrets
from typing import Protocol
import uuid

from google.api_core.exceptions import GoogleAPICallError, NotFound
import google.auth
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request

from app.config import settings
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.document_transform.schemas import MarkdownSource, MarkdownUpload


_SIGNED_URL_LIFETIME = timedelta(minutes=10)
_CONTENT_TYPE = "text/markdown"


class _StorageBlob(Protocol):
    size: int | None
    generation: int | str | None

    def generate_signed_url(self, **kwargs: object) -> str: ...

    def reload(self) -> None: ...

    def download_as_bytes(self, **kwargs: object) -> bytes: ...


class _SigningCredentials(Protocol):
    token: str | None

    def refresh(self, request: Request) -> None: ...


class _StorageBucket(Protocol):
    def blob(self, _blob_name: str, /) -> _StorageBlob: ...


class _StorageClient(Protocol):
    def bucket(self, _bucket_name: str, /) -> _StorageBucket: ...


class UploadUnavailableError(Exception):
    pass


class UploadNotReadyError(Exception):
    pass


def _blob_name(owner: uuid.UUID, upload_id: uuid.UUID) -> str:
    return f"markdown/{owner}/{upload_id}"


def _client() -> _StorageClient:
    factory: Callable[[], _StorageClient] = import_module("google.cloud.storage").Client
    return factory()


async def sign_markdown_upload(owner: uuid.UUID) -> MarkdownUpload:
    bucket = settings.DOCUMENT_TRANSFORM_UPLOAD_BUCKET
    signer = settings.DOCUMENT_TRANSFORM_UPLOAD_SIGNER
    if not bucket or not signer:
        raise UploadUnavailableError
    upload_id = uuid.UUID(hex=secrets.token_hex(16))
    return await asyncio.to_thread(_sign, bucket, signer, owner, upload_id)


def _sign(bucket: str, signer: str, owner: uuid.UUID, upload_id: uuid.UUID) -> MarkdownUpload:
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        signer_credentials: _SigningCredentials = credentials
        signer_credentials.refresh(Request())
    except GoogleAuthError as exc:
        raise UploadUnavailableError from exc
    token = credentials.token
    if not token:
        raise UploadUnavailableError
    headers = {
        "Content-Type": _CONTENT_TYPE,
        "x-goog-content-length-range": f"1,{MAX_MARKDOWN_BYTES}",
        "x-goog-if-generation-match": "0",
    }
    try:
        blob = _client().bucket(bucket).blob(_blob_name(owner, upload_id))
        url = blob.generate_signed_url(
            version="v4",
            expiration=_SIGNED_URL_LIFETIME,
            method="PUT",
            content_type=_CONTENT_TYPE,
            headers={key: value for key, value in headers.items() if key != "Content-Type"},
            service_account_email=signer,
            access_token=token,
        )
    except (GoogleAPICallError, GoogleAuthError) as exc:
        raise UploadUnavailableError from exc
    return MarkdownUpload(
        upload_id=upload_id,
        url=url,
        headers=headers,
        expires_in_seconds=int(_SIGNED_URL_LIFETIME.total_seconds()),
    )


async def load_markdown_upload(owner: uuid.UUID, source: MarkdownSource) -> MarkdownSource:
    bucket = settings.DOCUMENT_TRANSFORM_UPLOAD_BUCKET
    if not bucket:
        raise UploadUnavailableError
    return await asyncio.to_thread(_load, bucket, owner, source)


def _load(bucket: str, owner: uuid.UUID, source: MarkdownSource) -> MarkdownSource:
    if source.kind != "uploaded_markdown" or source.upload_id is None:
        msg = "An uploaded Markdown reference is required"
        raise ValueError(msg)
    try:
        blob = _client().bucket(bucket).blob(_blob_name(owner, source.upload_id))
        blob.reload()
    except NotFound as exc:
        raise UploadNotReadyError from exc
    except (GoogleAPICallError, GoogleAuthError) as exc:
        raise UploadUnavailableError from exc
    if not blob.size:
        raise UnsupportedSourceError
    if blob.size > MAX_MARKDOWN_BYTES:
        raise SourceTooLargeError
    if blob.generation is None:
        raise UploadUnavailableError
    try:
        data = blob.download_as_bytes(if_generation_match=int(blob.generation), raw_download=True)
    except (GoogleAPICallError, GoogleAuthError) as exc:
        raise UploadUnavailableError from exc
    if len(data) > MAX_MARKDOWN_BYTES:
        raise SourceTooLargeError
    try:
        content = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnsupportedSourceError from exc
    return MarkdownSource(
        kind="markdown",
        content=content,
        name=source.name,
        destination_folder_id=source.destination_folder_id,
    )
