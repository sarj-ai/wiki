from importlib.metadata import version

from app.config import settings
from app.main import create_app


def test_runtime_docs_are_disabled_by_default() -> None:
    application = create_app(settings.model_copy(update={"API_DOCS_ENABLED": False}))

    assert application.docs_url is None
    assert application.redoc_url is None
    assert application.openapi_url is None


def test_runtime_docs_require_explicit_opt_in() -> None:
    application = create_app(settings.model_copy(update={"API_DOCS_ENABLED": True}))

    assert application.docs_url == "/docs"
    assert application.redoc_url == "/redoc"
    assert application.openapi_url == "/openapi.json"


def test_api_version_comes_from_package_metadata() -> None:
    application = create_app(settings)

    assert application.version == version("sarj-docs-backend")
