from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from app.model_batches.service import ModelBatchCoordinator, ModelBatchStore


@dataclass(frozen=True, slots=True)
class OnlineModelTransport:
    kind: ClassVar[Literal["online"]] = "online"


@dataclass(frozen=True, slots=True)
class BatchModelTransport:
    kind: ClassVar[Literal["batch"]] = "batch"

    store: ModelBatchStore
    coordinator: ModelBatchCoordinator


type DocumentModelTransport = OnlineModelTransport | BatchModelTransport
