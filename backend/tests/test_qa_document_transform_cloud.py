from pathlib import Path
import subprocess
import sys


def test_cloud_entrypoint_reaches_explicit_fail_closed_data_plane_error() -> None:
    environment = {
        "PATH": "/usr/bin:/bin",
        "RELEASE_SHA": "a" * 40,
        "WIKI_QA_IMAGE_DIGEST": "sha256:" + "b" * 64,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.qa_document_transform_cloud",
            "--expected-release-sha",
            "a" * 40,
            "--source-folder-id",
            "source",
            "--destination-folder-id",
            "destination",
            "--mode",
            "both",
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert '"error": "backend_capability_data_plane_not_implemented"' in result.stderr
    assert "DATABASE_URL" not in result.stderr
    assert "SECRET_KEY" not in result.stderr
