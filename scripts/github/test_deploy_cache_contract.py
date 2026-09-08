from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
_CACHE_RESTORE_ACTION = "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_CACHE_SAVE_ACTION = "actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
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


def test_component_ci_runs_once_on_main_through_its_deploy_workflow() -> None:
    components = {
        "backend": "backend/**",
        "frontend": "frontend/**",
    }

    for component, source_path in components.items():
        ci_name = f"ci-{component}.yml"
        deploy_name = f"deploy-{component}.yml"
        ci_workflow = _workflow(ci_name)
        ci_triggers = ci_workflow["on"]
        deploy_workflow = _workflow(deploy_name)
        deploy_push = deploy_workflow["on"]["push"]
        callable_job = deploy_workflow["jobs"][f"ci-{component}"]

        assert "workflow_call" in ci_triggers
        assert "pull_request" in ci_triggers
        assert "push" not in ci_triggers
        assert deploy_push["branches"] == ["main"]
        assert source_path in deploy_push["paths"]
        assert f".github/workflows/{ci_name}" in deploy_push["paths"]
        assert callable_job["uses"] == f"./.github/workflows/{ci_name}"
        assert f"ci-{component}" in deploy_workflow["jobs"]["deploy"]["needs"]


def test_standalone_quality_skips_only_exact_domain_deploy_paths_on_main() -> None:
    quality = _workflow("ci-quality.yml")
    ignored = set(quality["on"]["push"]["paths-ignore"])
    deploy_paths = {
        path
        for workflow_name in ("deploy-backend.yml", "deploy-frontend.yml")
        for path in _workflow(workflow_name)["on"]["push"]["paths"]
    }

    assert ignored == deploy_paths
    assert quality["on"]["push"]["branches"] == ["main"]
    assert quality["on"]["pull_request"] is None
    assert quality["on"]["workflow_call"] is None


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


def test_frontend_build_restores_next_cache_but_only_trusted_main_saves() -> None:
    workflow = _workflow("ci-frontend.yml")
    assert workflow["permissions"] == {"contents": "read"}
    build = workflow["jobs"]["build"]
    assert "permissions" not in build
    steps = build["steps"]
    restore = _named_step(steps, "Restore Next.js incremental build cache")
    save = _named_step(steps, "Save trusted main Next.js incremental build cache")
    expected_path = "frontend/.next/cache"
    expected_prefix = (
        "nextjs-v1-${{ runner.os }}-${{ runner.arch }}-"
        "${{ hashFiles('frontend/package-lock.json') }}-"
    )

    assert restore["id"] == "next-cache"
    assert restore["uses"] == _CACHE_RESTORE_ACTION
    assert restore["with"] == {
        "path": expected_path,
        "key": expected_prefix + "${{ github.sha }}",
        "restore-keys": expected_prefix + "\n",
    }
    assert save["uses"] == _CACHE_SAVE_ACTION
    assert save["if"] == (
        "${{ success() && github.event_name == 'push' && "
        "github.ref == 'refs/heads/main' && "
        "steps.next-cache.outputs.cache-hit != 'true' }}"
    )
    assert save["with"] == {
        "path": expected_path,
        "key": "${{ steps.next-cache.outputs.cache-primary-key }}",
    }
    assert (
        _step_index(steps, "Restore Next.js incremental build cache")
        < _step_index(steps, "Install dependencies")
        < _step_index(steps, "Build")
        < _step_index(steps, "Save trusted main Next.js incremental build cache")
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
