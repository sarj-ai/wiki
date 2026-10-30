from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from loguru import logger
import pytest

from app.document_transform import google_gateway as module
from app.document_transform.errors import DocumentGatewayError
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.identifiers import DocsRevisionId, FileId


@pytest.mark.parametrize("provider_failed", [False, True])
@pytest.mark.asyncio
async def test_stage_marker_failure_logs_only_safe_shape(
    monkeypatch: pytest.MonkeyPatch, provider_failed: bool
) -> None:
    marker = "private-marker-secret"
    document: dict[str, Any] = {
        "revisionId": "private-revision",
        "title": "private-title",
        "tabs": [
            {
                "documentTab": {
                    "namedRanges": {
                        marker: {
                            "namedRanges": [
                                {
                                    "name": marker,
                                    "namedRangeId": "private-range-id",
                                    "ranges": [
                                        {
                                            "startIndex": 8765,
                                            "endIndex": 8766,
                                            "segmentId": "private-segment",
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                },
                "tabProperties": {"tabId": "private-tab"},
            }
        ],
    }
    gateway = object.__new__(GoogleDocumentGateway)
    monkeypatch.setattr(
        gateway,
        "_docs",
        lambda: SimpleNamespace(
            documents=lambda: SimpleNamespace(batchUpdate=lambda **_kwargs: object())
        ),
    )
    monkeypatch.setattr(module, "_google_http_request", lambda value, **kwargs: value(**kwargs))
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _document_id: document)

    def mutate(_request: object) -> dict[str, Any]:
        if provider_failed:
            raise DocumentGatewayError(http_status=503)
        return {}

    monkeypatch.setattr(gateway, "_execute_mutation_dict", mutate)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        with pytest.raises(DocumentGatewayError):
            await gateway.apply_in_place_stage(
                document_id=FileId("private-document"),
                required_revision_id=DocsRevisionId("private-revision"),
                marker_name=marker,
                marker_range={"startIndex": 9876, "endIndex": 9877, "tabId": "private-tab"},
                requests=[{"createHeader": {"type": "DEFAULT"}}],
            )
    finally:
        logger.remove(sink)
    output = "".join(messages)
    assert "revision_unchanged=True" in output
    assert "'definition_count': 1" in output
    assert "'range_count': 1" in output
    assert "'start_present_count': 1" in output
    assert "'matching_start_count': 0" in output
    assert "'exact_range_count': 0" in output
    if provider_failed:
        assert "http_status=503" in output
    assert "private-" not in output
    for index in (8765, 8766, 9876, 9877):
        assert str(index) not in output
