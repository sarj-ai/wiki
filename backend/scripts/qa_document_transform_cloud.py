"""Dependency-minimal, fail-closed Cloud Run entrypoint for corpus QA."""

import argparse
import json
import re
import sys

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.document_transform.qa_contract import PRODUCTION_QA_EXCHANGE_URL


_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class _RuntimeBinding(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    release_sha: str = Field(default="", validation_alias="RELEASE_SHA")
    image_digest: str = Field(default="", validation_alias="WIKI_QA_IMAGE_DIGEST")
    exchange_url: str = Field(default="", validation_alias="WIKI_QA_EXCHANGE_URL")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--source-folder-id", required=True)
    parser.add_argument("--destination-folder-id", required=True)
    parser.add_argument("--mode", choices=("format", "unslop", "both"), required=True)
    arguments = parser.parse_args()
    runtime = _RuntimeBinding()
    if (
        not _SHA.fullmatch(runtime.release_sha)
        or arguments.expected_release_sha != runtime.release_sha
        or not _DIGEST.fullmatch(runtime.image_digest)
        or runtime.exchange_url != PRODUCTION_QA_EXCHANGE_URL
        or arguments.source_folder_id == arguments.destination_folder_id
    ):
        _fail("runtime_binding_preflight_failed")
    _fail("backend_capability_data_plane_not_implemented")


def _fail(code: str) -> None:
    print(json.dumps({"error": code, "status": "failed"}, sort_keys=True), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
