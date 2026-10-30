from __future__ import annotations

import pytest

from app.files.router import read_html_content
from app.files.service import FilesService
from app.identifiers import FileId
from tests.support import dependency


class _HtmlFiles:
    async def get_html_content(self, file_id: FileId) -> bytes:
        assert file_id == "chart"
        return b"<!doctype html><script>document.body.dataset.ready='yes'</script>"


@pytest.mark.asyncio
async def test_html_response_is_doubly_sandboxed_and_never_cached() -> None:
    response = await read_html_content(
        FileId("chart"),
        dependency(_HtmlFiles()),
    )

    assert bytes(response.body).startswith(b"<!doctype html>")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    csp = response.headers["content-security-policy"]
    assert "sandbox allow-popups allow-popups-to-escape-sandbox allow-scripts" in csp
    assert "connect-src 'none'" in csp
    assert "form-action 'none'" in csp
    assert "frame-ancestors" in csp
    assert "allow-same-origin" not in csp
    assert "allow-top-navigation" not in csp


def test_html_service_contract_stays_on_files_service() -> None:
    assert "get_html_content" in FilesService.__abstractmethods__
    assert callable(getattr(_HtmlFiles(), "get_html_content", None))
