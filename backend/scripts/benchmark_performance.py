import asyncio
from functools import partial
import gzip
import json
from statistics import median
from time import perf_counter
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, FastAPI
import httpx
from pydantic import BaseModel

from app.drive.schemas import DriveNode, DriveTree
from app.identifiers import DriveId, FileId


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class DispatchRead(BaseModel):
    value: str


class DispatchRouter:
    def __init__(
        self,
        sync_factory: Callable[[], str],
        async_factory: Callable[[], Awaitable[str]],
    ) -> None:
        self._sync_factory = sync_factory
        self._async_factory = async_factory

    def build(self) -> APIRouter:
        router = APIRouter()

        @router.get("/sync", response_model=DispatchRead, status_code=200)
        async def sync_route(value: Annotated[str, Depends(self._sync_factory)]) -> DispatchRead:
            return DispatchRead(value=value)

        @router.get("/async", response_model=DispatchRead, status_code=200)
        async def async_route(value: Annotated[str, Depends(self._async_factory)]) -> DispatchRead:
            return DispatchRead(value=value)

        return router


def main() -> None:
    tree = _tree()
    payload = tree.model_dump_json().encode()
    result: dict[str, object] = {
        "fixture_nodes": len(tree.files),
        "tree_bytes": len(payload),
        "tree_serialize_ms": _measure(tree.model_dump_json),
        "tree_validate_ms": _measure(lambda: DriveTree.model_validate_json(payload)),
        "dependency_dispatch": asyncio.run(_dispatch()),
    }
    for level in (1, 4, 6, 9):
        compressed = gzip.compress(payload, compresslevel=level)
        result[f"gzip_{level}"] = {
            "bytes": len(compressed),
            "median_ms": _measure(partial(gzip.compress, payload, compresslevel=level)),
        }
    print(json.dumps(result, indent=2))


def _measure(fn: Callable[[], object], repeats: int = 20) -> float:
    samples: list[float] = []
    for _ in range(repeats):
        started = perf_counter()
        fn()
        samples.append((perf_counter() - started) * 1000)
    return round(median(samples), 3)


def _tree() -> DriveTree:
    return DriveTree(
        drive_id=DriveId("benchmark-drive"),
        folders=[],
        files=[
            DriveNode(
                id=FileId(f"file-{index}"),
                name=f"Knowledge base document {index}",
                mime_type="application/vnd.google-apps.document",
                parent_id=FileId(f"folder-{index % 100}"),
                modified_time=None,
            )
            for index in range(10_000)
        ],
        files_complete=True,
    )


async def _dispatch() -> dict[str, float]:
    app = FastAPI()

    def sync_factory() -> str:
        return "ready"

    async def async_factory() -> str:
        return await asyncio.sleep(0, result="ready")

    app.include_router(DispatchRouter(sync_factory, async_factory).build())

    result: dict[str, float] = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://bench"
    ) as client:
        for path in ("/sync", "/async"):
            samples: list[float] = []
            for _ in range(200):
                started = perf_counter()
                response = await client.get(path)
                response.raise_for_status()
                samples.append((perf_counter() - started) * 1000)
            result[f"{path[1:]}_dependency_ms"] = round(median(samples), 3)
    return result


if __name__ == "__main__":
    main()
