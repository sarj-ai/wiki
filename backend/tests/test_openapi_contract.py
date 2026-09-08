import json
from typing import Any

from app.config import settings
from app.main import create_app


_HTTP_METHODS = {"delete", "get", "patch", "post", "put"}


def _schema() -> dict[str, Any]:
    return create_app(settings).openapi()


def test_client_schema_excludes_runtime_only_and_removed_operations() -> None:
    paths = _schema()["paths"]

    assert "/health" not in paths
    assert "/auth/login" not in paths
    assert "/auth/callback" not in paths
    assert "/internal/tasks/smart-create" not in paths
    assert "get" not in paths["/extras/doc-icons/{drive_id}"]
    assert "/extras/sort-order/{drive_id}" not in paths


def test_client_schema_excludes_internal_qa_acl_contract() -> None:
    rendered = json.dumps(_schema(), sort_keys=True)

    assert "qa_destination_permission" not in rendered
    assert "qa_permission_evidence" not in rendered
    assert "QaDestinationPermissionBinding" not in rendered
    assert "QaCopyPermissionEvidence" not in rendered


def test_operations_have_explicit_unique_ids() -> None:
    operations = [
        operation
        for path in _schema()["paths"].values()
        for method, operation in path.items()
        if method in _HTTP_METHODS
    ]
    operation_ids = [operation["operationId"] for operation in operations]

    assert len(operation_ids) == len(set(operation_ids))
    assert all("_get" not in operation_id for operation_id in operation_ids)


def test_protected_operation_accepts_cookie_or_bearer_authentication() -> None:
    schema = _schema()

    assert schema["components"]["securitySchemes"] == {
        "APIKeyCookie": {"type": "apiKey", "in": "cookie", "name": "access_token"},
        "HTTPBearer": {"type": "http", "scheme": "bearer"},
        "RefreshCookie": {"type": "apiKey", "in": "cookie", "name": "refresh_token"},
    }
    assert schema["paths"]["/auth/me"]["get"]["security"] == [
        {"APIKeyCookie": []},
        {"HTTPBearer": []},
    ]
    assert schema["paths"]["/auth/refresh"]["post"]["security"] == [{"RefreshCookie": []}]


def test_non_json_and_conditional_responses_are_described() -> None:
    paths = _schema()["paths"]

    content = paths["/files/{file_id}/content"]["get"]["responses"]["200"]["content"]
    assert content == {"text/html": {"schema": {"type": "string"}}}
    assert "304" in paths["/sidebar/drives/{drive_id}/folders"]["get"]["responses"]
