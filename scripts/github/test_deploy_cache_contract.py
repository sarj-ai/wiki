import json
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
_READY_FENCE_COUNTS = {"backend": 1, "frontend": 2}


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


def test_deploys_trust_exact_sha_gates_without_repeating_generic_validation() -> None:
    duplicate_steps = {
        "deploy-backend.yml": {
            "Restore prerequisite pre-commit environments",
            "Set up Python",
            "Install dependencies",
            "Run pre-commit custom standards",
            "Ruff check",
            "Ruff format check",
            "Identify Mypy cache toolchain",
            "Restore prerequisite Mypy incremental data",
            "Mypy strict",
            "Basedpyright strict",
            "ty check",
            "Vulture dead-code check",
            "Import contracts",
            "pip-audit",
            "Unit tests",
        },
        "deploy-frontend.yml": {
            "Restore prerequisite pre-commit environments",
            "Install analysis and frontend dependencies",
            "Bootstrap analysis inputs",
            "Run pre-commit custom standards",
            "Prettier check",
            "TypeScript check",
            "ESLint",
            "Knip",
            "Type coverage",
            "npm audit",
            "Run vitest",
        },
    }
    retained_release_steps = {
        "deploy-backend.yml": {
            "Build and push image",
            "Trivy image scan",
            "Deploy candidate without traffic",
            "Run database migrations once",
            "Switch production traffic",
            "Verify revision and production health",
            "Resolve native MCP mode from exact revision",
            "Verify native MCP production contract",
            "Verify correlated production logs",
            "Report blocked release without rollback",
        },
        "deploy-frontend.yml": {
            "Build for Cloudflare Workers",
            "Capture current production version",
            "Deploy to Cloudflare",
            "Verify exact production revision",
            "Roll back an unverified frontend release",
        },
    }

    for workflow_name, forbidden in duplicate_steps.items():
        deploy = _workflow(workflow_name)["jobs"]["deploy"]
        steps = deploy["steps"]
        names = {step.get("name") for step in steps}
        commands = "\n".join(str(step.get("run", "")) for step in steps)

        assert set(deploy["needs"]) == _DEPLOY_NEEDS
        assert names.isdisjoint(forbidden)
        assert retained_release_steps[workflow_name] <= names
        if workflow_name == "deploy-backend.yml":
            assert not any(
                command in commands
                for command in (
                    "pre-commit run",
                    "ruff check",
                    "ruff format",
                    "mypy",
                    "basedpyright",
                    "ty check",
                    "vulture",
                    "lint-imports",
                    "pip-audit",
                    "pytest",
                    "uv python install",
                    "uv sync",
                )
            )
        else:
            assert not any(
                command in commands
                for command in (
                    "pre-commit run",
                    "format:check",
                    "run typecheck",
                    "run lint",
                    "run knip",
                    "type-coverage",
                    "npm audit",
                    "npm test",
                    "run generate:api",
                    "uv sync",
                )
            )

    frontend_steps = _workflow("deploy-frontend.yml")["jobs"]["deploy"]["steps"]
    frontend_install = _named_step(frontend_steps, "Install frontend dependencies")
    assert frontend_install["run"] == "npm ci --prefix frontend"
    assert "generate:api" in _workflow_script("frontend/package.json", "postinstall")


def _workflow_script(path: str, name: str) -> str:
    package = json.loads((ROOT / path).read_text())
    return str(package["scripts"][name])


def test_deploy_ready_fences_remain_before_credentials_and_mutation() -> None:
    for component in ("backend", "frontend"):
        steps = _workflow(f"deploy-{component}.yml")["jobs"]["deploy"]["steps"]
        ready = [
            index
            for index, step in enumerate(steps)
            if step.get("run")
            == f"bash scripts/github/verify-release-gate.sh ready {component}"
        ]
        assert len(ready) == _READY_FENCE_COUNTS[component]

        if component == "backend":
            auth = next(
                index
                for index, step in enumerate(steps)
                if step.get("uses", "").startswith("google-github-actions/auth@")
            )
            assert ready[0] < auth < _step_index(steps, "Deploy candidate without traffic")
        else:
            build = _step_index(steps, "Build for Cloudflare Workers")
            capture = _step_index(steps, "Capture current production version")
            deploy = _step_index(steps, "Deploy to Cloudflare")
            assert ready[0] < build < ready[1] < capture < deploy


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
