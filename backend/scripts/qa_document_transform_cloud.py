"""Dependency-minimal, fail-closed Cloud Run entrypoint for corpus QA."""

import argparse
import json
import os
import re
import sys


_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--source-folder-id", required=True)
    parser.add_argument("--destination-folder-id", required=True)
    parser.add_argument("--mode", choices=("format", "unslop", "both"), required=True)
    arguments = parser.parse_args()
    release = os.environ.get("RELEASE_SHA", "")  # ruff: ignore[banned-api] -- stdlib-only bootstrap.
    image_digest = os.environ.get(  # ruff: ignore[banned-api] -- stdlib-only bootstrap.
        "WIKI_QA_IMAGE_DIGEST", ""
    )
    if (
        not _SHA.fullmatch(release)
        or arguments.expected_release_sha != release
        or not _DIGEST.fullmatch(image_digest)
        or arguments.source_folder_id == arguments.destination_folder_id
    ):
        _fail("runtime_binding_preflight_failed")
    _fail("backend_capability_data_plane_not_implemented")


def _fail(code: str) -> None:
    print(json.dumps({"error": code, "status": "failed"}, sort_keys=True), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
