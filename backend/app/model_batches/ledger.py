from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, assert_never

from google.genai import errors as genai_errors, types as genai_types

from app.model_batches.requests import ModelRequest
from app.model_batches.states import (
    FailedModelCall,
    ModelCall,
    PendingModelCall,
    QueuedModelCall,
    SucceededModelCall,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterable

    from app.identifiers import ModelRequestHash


type ModelCallKey = tuple[ModelRequestHash, int]


@dataclass(frozen=True, slots=True)
class MissedModelCall:
    request: ModelRequest
    occurrence: int

    @property
    def key(self) -> ModelCallKey:
        return (self.request.request_sha256, self.occurrence)


class ModelBatchPendingError(Exception):
    def __init__(self, misses: tuple[MissedModelCall, ...]) -> None:
        if not misses:
            msg = "A pending model batch requires at least one request"
            raise ValueError(msg)
        super().__init__(f"{len(misses)} model request(s) await batch prediction")
        self.misses = misses

    @classmethod
    def merge(cls, errors: Iterable[ModelBatchPendingError]) -> ModelBatchPendingError:
        unique = {miss.key: miss for error in errors for miss in error.misses}
        return cls(tuple(unique.values()))


class ModelCallLedger:
    def __init__(self, calls: Mapping[ModelCallKey, ModelCall]) -> None:
        self._calls = dict(calls)
        self._occurrences: Counter[ModelRequestHash] = Counter()

    def resolve(self, request: ModelRequest) -> genai_types.GenerateContentResponse:
        digest = request.request_sha256
        self._occurrences[digest] += 1
        miss = MissedModelCall(request=request, occurrence=self._occurrences[digest])
        match self._calls.get(miss.key):
            case None | PendingModelCall() | QueuedModelCall():
                raise ModelBatchPendingError((miss,))
            case SucceededModelCall(outcome=outcome):
                return genai_types.GenerateContentResponse.model_validate(outcome.response)
            case FailedModelCall(outcome=outcome):
                failure = outcome.failure
                raise genai_errors.APIError(
                    failure.code,
                    {"error": {"code": failure.code, "message": failure.message}},
                )
            case unreachable:
                assert_never(unreachable)


_ledger_var: ContextVar[ModelCallLedger | None] = ContextVar(
    "document_model_call_ledger", default=None
)


@contextmanager
def model_call_ledger_scope(ledger: ModelCallLedger) -> Generator[None]:
    token = _ledger_var.set(ledger)
    try:
        yield
    finally:
        _ledger_var.reset(token)


def current_model_call_ledger() -> ModelCallLedger | None:
    return _ledger_var.get()


async def collect_pending[T](work: Awaitable[T]) -> T | ModelBatchPendingError:
    try:
        return await work
    except ModelBatchPendingError as error:
        return error


def require_answered[T](results: Iterable[T | ModelBatchPendingError]) -> list[T]:
    answered: list[T] = []
    pending: list[ModelBatchPendingError] = []
    for result in results:
        if isinstance(result, ModelBatchPendingError):
            pending.append(result)
        else:
            answered.append(result)
    if pending:
        raise ModelBatchPendingError.merge(pending)
    return answered


async def no_retry_pause(_seconds: float) -> None:
    await asyncio.sleep(0)


@dataclass(frozen=True, slots=True)
class OnlinePacing:
    kind: ClassVar[Literal["online"]] = "online"


@dataclass(frozen=True, slots=True)
class BatchPacing:
    kind: ClassVar[Literal["batch"]] = "batch"

    editorial_passes: int = 2
    layout_passes: int = 1


type ModelPacing = OnlinePacing | BatchPacing


def current_model_pacing() -> ModelPacing:
    return OnlinePacing() if current_model_call_ledger() is None else BatchPacing()
