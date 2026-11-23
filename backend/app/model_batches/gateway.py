from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from importlib import import_module
import json
import re
from typing import TYPE_CHECKING, Protocol, override
import uuid

from google import genai
from google.genai import errors as genai_errors, types as genai_types
from pydantic import JsonValue

from app.identifiers import ModelBatchJobId, VertexBatchJobName
from app.model_batches.states import ProviderJobState, provider_job_state


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from app.model_batches.states import (
        RunningModelBatchJob,
        SubmittingModelBatchJob,
    )


_PREFIX = "model-batches"
_OUTPUT_OBJECT = re.compile(rf"^{_PREFIX}/([0-9a-f-]{{36}})/output/.+\.jsonl$")
_DISPLAY_NAME_PREFIX = "wiki-model-batch-"
_TRANSIENT_CLIENT_STATUSES = frozenset({408, 429})


class _Blob(Protocol):
    name: str

    def upload_from_string(self, data: bytes, *, content_type: str) -> None: ...

    def download_as_bytes(self) -> bytes: ...


class _Bucket(Protocol):
    def blob(self, _blob_name: str, /) -> _Blob: ...

    def list_blobs(self, *, prefix: str) -> Iterable[_Blob]: ...


class _StorageClient(Protocol):
    def bucket(self, _bucket_name: str, /) -> _Bucket: ...


def _storage_client(project: str) -> _StorageClient:
    factory: Callable[..., _StorageClient] = import_module("google.cloud.storage").Client
    return factory(project=project)


class ModelBatchSubmissionRejectedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ModelBatchLocation:
    project: str
    location: str
    bucket: str

    def input_uri(self, job_id: ModelBatchJobId) -> str:
        return f"gs://{self.bucket}/{_PREFIX}/{job_id}/input.jsonl"

    def output_prefix(self, job_id: ModelBatchJobId) -> str:
        return f"gs://{self.bucket}/{_PREFIX}/{job_id}/output"


def job_id_from_output_object(object_name: str) -> ModelBatchJobId | None:
    match = _OUTPUT_OBJECT.fullmatch(object_name)
    if match is None:
        return None
    return ModelBatchJobId(uuid.UUID(match.group(1)))


def display_name(job_id: ModelBatchJobId) -> str:
    return f"{_DISPLAY_NAME_PREFIX}{job_id}"


class ModelBatchGateway(ABC):
    @abstractmethod
    async def write_input(
        self, job_id: ModelBatchJobId, lines: Sequence[dict[str, JsonValue]]
    ) -> None: ...

    @abstractmethod
    async def create(self, job: SubmittingModelBatchJob) -> VertexBatchJobName: ...

    @abstractmethod
    async def find(self, job: SubmittingModelBatchJob) -> VertexBatchJobName | None: ...

    @abstractmethod
    async def observe(self, job: RunningModelBatchJob) -> ProviderJobState: ...

    @abstractmethod
    async def read_output(self, output_directory: str) -> list[dict[str, JsonValue]]: ...


class VertexModelBatchGateway(ModelBatchGateway):
    def __init__(
        self,
        *,
        location: ModelBatchLocation,
        client: genai.Client | None = None,
        storage_client: _StorageClient | None = None,
    ) -> None:
        self._location = location
        self._client = client or genai.Client(
            vertexai=True, project=location.project, location=location.location
        )
        self._storage = storage_client

    def _bucket(self) -> _Bucket:
        if self._storage is None:
            self._storage = _storage_client(self._location.project)
        return self._storage.bucket(self._location.bucket)

    @override
    async def write_input(
        self, job_id: ModelBatchJobId, lines: Sequence[dict[str, JsonValue]]
    ) -> None:
        body = "".join(
            json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n" for line in lines
        )
        blob = self._bucket().blob(f"{_PREFIX}/{job_id}/input.jsonl")
        await asyncio.to_thread(
            blob.upload_from_string, body.encode(), content_type="application/jsonl"
        )

    @override
    async def create(self, job: SubmittingModelBatchJob) -> VertexBatchJobName:
        try:
            created = await self._client.aio.batches.create(
                model=job.model,
                src=self._location.input_uri(job.id),
                config=genai_types.CreateBatchJobConfig(
                    display_name=display_name(job.id),
                    dest=self._location.output_prefix(job.id),
                ),
            )
        except genai_errors.ClientError as error:
            if error.code in _TRANSIENT_CLIENT_STATUSES:
                raise  # Ambiguous: recovery looks the job up by display name.
            raise ModelBatchSubmissionRejectedError from error
        if not created.name:
            msg = "Vertex AI created a batch job without a resource name"
            raise ValueError(msg)
        return VertexBatchJobName(created.name)

    @override
    async def find(self, job: SubmittingModelBatchJob) -> VertexBatchJobName | None:
        pager = await self._client.aio.batches.list(
            config=genai_types.ListBatchJobsConfig(
                filter=f'display_name="{display_name(job.id)}"', page_size=2
            )
        )
        names = [found.name async for found in pager if found.name]
        if len(names) > 1:
            msg = "More than one Vertex batch job claims the same display name"
            raise ValueError(msg)
        return VertexBatchJobName(names[0]) if names else None

    @override
    async def observe(self, job: RunningModelBatchJob) -> ProviderJobState:
        return provider_job_state(await self._client.aio.batches.get(name=job.vertex_job_name))

    @override
    async def read_output(self, output_directory: str) -> list[dict[str, JsonValue]]:
        bucket_name, _, prefix = output_directory.removeprefix("gs://").partition("/")
        if bucket_name != self._location.bucket:
            msg = "Vertex batch output is outside the configured bucket"
            raise ValueError(msg)

        def read() -> list[dict[str, JsonValue]]:
            lines: list[dict[str, JsonValue]] = []
            for blob in self._bucket().list_blobs(prefix=prefix.rstrip("/") + "/"):
                if not blob.name.endswith(".jsonl"):
                    continue
                for raw in blob.download_as_bytes().splitlines():
                    if raw.strip():
                        parsed = json.loads(raw)
                        if not isinstance(parsed, dict):
                            msg = "Vertex batch output line is not an object"
                            raise TypeError(msg)
                        lines.append(parsed)
            return lines

        return await asyncio.to_thread(read)
