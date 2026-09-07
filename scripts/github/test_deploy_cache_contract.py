from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
_CACHE_RESTORE_ACTION = "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_DEPLOY_NEEDS = {
    "ci-backend",
    "ci-frontend",
    "ci-quality",
    "ci-iac",
    "standards-receipt",
}


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def _named_step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


def _step_index(steps: list[dict[str, Any]], name: str) -> int:
    return steps.index(_named_step(steps, name))


def test_deploys_restore_the_prerequisite_pre_commit_cache() -> None:
    quality_steps = _workflow("ci-quality.yml")["jobs"]["pre-commit"]["steps"]
    producer = _named_step(quality_steps, "Cache pre-commit environments")
    expected_cache = {
        "path": producer["with"]["path"],
        "key": producer["with"]["key"],
    }

    for name in ("deploy-backend.yml", "deploy-frontend.yml"):
        deploy = _workflow(name)["jobs"]["deploy"]
        steps = deploy["steps"]
        consumer = _named_step(steps, "Restore prerequisite pre-commit environments")

        assert set(deploy["needs"]) == _DEPLOY_NEEDS
        assert consumer["uses"] == _CACHE_RESTORE_ACTION
        assert consumer["with"] == expected_cache
        assert _step_index(
            steps, "Restore prerequisite pre-commit environments"
        ) < _step_index(steps, "Run pre-commit custom standards")


def test_backend_deploy_restores_the_prerequisite_mypy_cache() -> None:
    backend_ci = _workflow("ci-backend.yml")
    producer_steps = backend_ci["jobs"]["lint-and-typecheck"]["steps"]
    producer = _named_step(producer_steps, "Restore Mypy incremental data")

    deploy = _workflow("deploy-backend.yml")["jobs"]["deploy"]
    steps = deploy["steps"]
    consumer = _named_step(steps, "Restore prerequisite Mypy incremental data")

    assert consumer["uses"] == producer["uses"] == _CACHE_RESTORE_ACTION
    assert consumer["with"] == producer["with"]
    assert (
        _step_index(steps, "Identify Mypy cache toolchain")
        < _step_index(steps, "Restore prerequisite Mypy incremental data")
        < _step_index(steps, "Mypy strict")
    )
    assert not any(
        step.get("uses", "").startswith("actions/cache/save@") for step in steps
    )


def test_required_ci_aggregators_remain_fail_closed() -> None:
    expected_needs = {
        "ci-backend.yml": {
            "lint-and-typecheck",
            "test",
            "postgres-concurrency",
            "image",
        },
        "ci-frontend.yml": {"lint-and-typecheck", "test", "build"},
        "ci-iac.yml": {"terraform-validation", "trivy-config"},
    }

    for name, needs in expected_needs.items():
        required = _workflow(name)["jobs"]["required"]
        assert required["if"] == "${{ always() }}"
        assert set(required["needs"]) == needs
