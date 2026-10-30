from pathlib import Path
import subprocess

import pytest


_SCRIPT = Path(__file__).parents[2] / "scripts/github/verify-smart-create-queue.sh"


@pytest.mark.parametrize(
    ("smart_state", "document_state", "expected_success"),
    [
        ("RUNNING", "RUNNING", True),
        ("PAUSED", "RUNNING", False),
        ("RUNNING", "PAUSED", False),
        ("RUNNING", "DISABLED", False),
        ("RUNNING", "", False),
        ("RUNNING", "ERROR", False),
    ],
)
def test_queue_gate_requires_both_running(
    smart_state: str,
    document_state: str,
    *,
    expected_success: bool,
) -> None:
    command = r"""
gcloud() {
  if [[ "$4" == "smart" ]]; then
    echo "$TEST_SMART_STATE"
  elif [[ "$TEST_DOCUMENT_STATE" == "ERROR" ]]; then
    return 1
  else
    echo "$TEST_DOCUMENT_STATE"
  fi
}
export -f gcloud
bash "$1"
"""
    result = subprocess.run(
        ["bash", "-c", command, "queue-gate-test", str(_SCRIPT)],
        env={
            "SMART_CREATE_QUEUE_NAME": "smart",
            "DOCUMENT_TRANSFORM_QUEUE_NAME": "document",
            "REGION": "test-region",
            "PROJECT_ID": "test-project",
            "TEST_SMART_STATE": smart_state,
            "TEST_DOCUMENT_STATE": document_state,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is expected_success
    if expected_success:
        assert "Queue smart is RUNNING" in result.stdout
        assert "Queue document is RUNNING" in result.stdout
