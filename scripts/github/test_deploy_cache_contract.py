import ast
from dataclasses import dataclass
import itertools
import json
import os
from pathlib import Path
import re
from typing import Any

import pytest
import yaml

from scripts.github.release_gate import (
    ReleaseBlockedError,
    command_output,
    require_ci_gates,
)


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
_JOB_RESULTS = ("success", "failure", "cancelled", "skipped")
_PORTABLE_RUNNER = "${{ vars.CI_RUNNER || 'ubuntu-24.04' }}"


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def _named_step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(step for step in steps if step.get("name") == name)


def _step_index(steps: list[dict[str, Any]], name: str) -> int:
    return steps.index(_named_step(steps, name))


def test_qualification_runners_work_without_private_services_and_allow_an_owned_override() -> None:
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        workflow = _workflow(path.name)
        for job in workflow["jobs"].values():
            if "runs-on" not in job:
                continue
            expected = (
                "ubuntu-24.04"
                if path.name in {"standards.yml", "commit-policy.yml"}
                else _PORTABLE_RUNNER
            )
            assert job["runs-on"] == expected


def test_ci_image_uses_a_portable_builder_and_retains_the_required_loaded_image_scan() -> None:
    image = _workflow("ci-backend.yml")["jobs"]["image"]
    steps = image["steps"]
    builder = next(
        step for step in steps if step.get("uses", "").startswith("docker/setup-buildx-action@")
    )
    build = _named_step(steps, "Build image")
    scan = _named_step(steps, "Trivy image scan")

    assert build["uses"] == "docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc"
    assert build["with"] == {
        "context": "backend",
        "push": False,
        "load": True,
        "tags": "backend:ci",
        "cache-from": "type=gha,scope=backend-ci",
        "cache-to": "type=gha,mode=max,scope=backend-ci",
        "provenance": False,
        "sbom": False,
    }
    assert steps.index(builder) < steps.index(build) < steps.index(scan)
    assert scan["with"]["image-ref"] == "backend:ci"
    assert scan["with"]["severity"] == "HIGH,CRITICAL"
    assert scan["with"]["exit-code"] == "1"
    assert not any(step.get("uses", "").startswith("useblacksmith/") for step in steps)
    assert all(
        "if" not in step and "continue-on-error" not in step for step in [builder, build, scan]
    )


def test_component_ci_runs_once_on_main_through_shared_release() -> None:
    components = {"frontend": "frontend/**"}

    for component, source_path in components.items():
        ci_name = f"ci-{component}.yml"
        deploy_name = f"deploy-{component}.yml"
        ci_workflow = _workflow(ci_name)
        ci_triggers = ci_workflow["on"]
        deploy_workflow = _workflow(deploy_name)
        release = _workflow("release-main.yml")
        deploy_push = release["on"]["push"]
        callable_job = release["jobs"][f"ci-{component}"]

        assert "workflow_call" in ci_triggers
        assert "pull_request" in ci_triggers
        assert "push" not in ci_triggers
        assert deploy_push["branches"] == ["main"]
        assert source_path in deploy_push["paths"]
        assert f".github/workflows/{ci_name}" in deploy_push["paths"]
        assert callable_job["uses"] == f"./.github/workflows/{ci_name}"
        assert "push" not in deploy_workflow["on"]
        assert "workflow_dispatch" in deploy_workflow["on"]
        assert f"ci-{component}" in deploy_workflow["jobs"]["deploy"]["needs"]


def test_backend_deploy_runs_only_for_production_inputs() -> None:
    ci_workflow = _workflow("ci-backend.yml")
    deploy_workflow = _workflow("deploy-backend.yml")
    release = _workflow("release-main.yml")
    deploy_paths = set(release["on"]["push"]["paths"])
    production_inputs = {
        "backend/.dockerignore",
        "backend/Dockerfile",
        "backend/alembic.ini",
        "backend/alembic/**",
        "backend/app/**",
        "backend/pyproject.toml",
        "backend/uv.lock",
        "backend/uv.toml",
    }

    assert ci_workflow["on"]["pull_request"] is None
    assert release["on"]["push"]["branches"] == ["main"]
    assert production_inputs <= deploy_paths
    assert "backend/**" not in deploy_paths
    assert ".github/workflows/ci-backend.yml" not in deploy_paths
    assert deploy_paths.isdisjoint(
        {
            "backend/tests/**",
            "backend/scripts/**",
            "backend/Dockerfile.qa",
        }
    )
    assert deploy_workflow["jobs"]["ci-backend"]["uses"] == ("./.github/workflows/ci-backend.yml")
    assert "ci-backend" in deploy_workflow["jobs"]["deploy"]["needs"]


def test_standalone_quality_preserves_pr_and_reusable_checks_and_fails_open() -> None:
    quality = _workflow("ci-quality.yml")
    assert set(quality["on"]["push"]) == {"branches", "paths-ignore"}
    assert quality["on"]["push"]["branches"] == ["main"]
    assert quality["on"]["pull_request"] is None
    assert quality["on"]["workflow_call"]["inputs"]["force_full"]["default"] is False
    router = quality["jobs"]["route-quality"]
    assert router["outputs"] == {"run_quality": "${{ steps.route.outputs.run_quality }}"}
    checkout, route = router["steps"]
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    assert route["id"] == "route"
    assert route["env"] == {
        "BASE_SHA": "${{ github.event.before }}",
        "HEAD_SHA": "${{ github.sha }}",
    }
    assert route["run"] == (
        'bash scripts/github/quality-ci-policy.sh --diff "$BASE_SHA" "$HEAD_SHA" >>"$GITHUB_OUTPUT"'
    )
    for job_id, name in {
        "repository-quality": "pre-commit (actionlint, hygiene, sarj rules)",
        "api-contract": "OpenAPI spec in sync",
        "secret-scan": "gitleaks (full history)",
    }.items():
        job = quality["jobs"][job_id]
        assert job["name"] == name
        assert job["needs"] == "route-quality"
        assert job["if"] == (
            "${{ !cancelled() && (needs.route-quality.result != 'success' || needs.route-quality.outputs.run_quality != 'false') }}"
        )
        assert "continue-on-error" not in job
    release = _workflow("release-main.yml")
    assert release["jobs"]["ci-quality"]["with"] == {"force_full": True}
    assert "if" not in release["jobs"]["ci-quality"]
    pre_commit = _named_step(quality["jobs"]["repository-quality"]["steps"], "Run pre-commit")
    assert pre_commit["env"]["FORCE_FULL"] == "${{ inputs.force_full }}"


@pytest.mark.parametrize(
    ("cancelled", "router_result", "output", "expected"),
    [
        (False, "success", "false", False),
        (False, "success", "true", True),
        (False, "success", "", True),
        (False, "success", "unknown", True),
        (False, "failure", "false", True),
        (False, "skipped", "", True),
        (False, "cancelled", "", True),
        (True, "success", "true", False),
        (True, "success", "false", False),
        (True, "failure", "false", False),
        (True, "skipped", "", False),
        (True, "cancelled", "", False),
    ],
)
def test_quality_dependency_contract_fails_open_and_honors_workflow_cancellation(
    *, cancelled: bool, router_result: str, output: str, expected: bool
) -> None:
    for job_id in ("repository-quality", "api-contract", "secret-scan"):
        condition = _workflow("ci-quality.yml")["jobs"][job_id]["if"]
        assert condition == (
            "${{ !cancelled() && (needs.route-quality.result != 'success' || needs.route-quality.outputs.run_quality != 'false') }}"
        )
        selected = not cancelled and (router_result != "success" or output != "false")
        assert selected is expected


@pytest.mark.parametrize(
    ("event", "ref", "inputs", "expected"),
    [
        ("push", "refs/heads/main", {}, True),
        ("push", "refs/heads/main", {"force_full": ""}, True),
        ("push", "refs/heads/main", {"force_full": False}, False),
        ("push", "refs/heads/main", {"force_full": True}, False),
        ("pull_request", "refs/pull/1/merge", {}, False),
        ("workflow_call", "refs/heads/main", {"force_full": False}, False),
        ("push", "refs/heads/topic", {}, False),
        ("workflow_dispatch", "refs/heads/main", {}, False),
    ],
)
def test_quality_router_selects_plain_main_pushes_from_typed_event_inputs(
    *, event: str, ref: str, inputs: dict[str, bool | str], expected: bool
) -> None:
    condition = _workflow("ci-quality.yml")["jobs"]["route-quality"]["if"]
    event_check, ref_check, input_check = condition.split(" && ")
    assert event_check == "github.event_name == 'push'"
    assert ref_check == "github.ref == 'refs/heads/main'"
    comparison = r"toJSON\(inputs\.force_full\) == '([^']*)'"
    assert re.sub(comparison, "INPUT", input_check) == "(INPUT || INPUT)"
    accepted_inputs = re.findall(comparison, input_check)
    serialized_input = json.dumps(inputs.get("force_full"))
    selected = event == "push" and ref == "refs/heads/main" and serialized_input in accepted_inputs
    assert selected is expected


def test_quality_router_matches_the_complete_release_path_contract() -> None:
    source = (ROOT / "scripts/github/quality-ci-policy.sh").read_text()
    patterns = source.split('case "$path" in\n', 1)[1].split(")\n", 1)[0]
    shell_paths = {part.strip() for part in patterns.replace("\\\n", "").split("|")}
    native_patterns = set(_workflow("release-main.yml")["on"]["push"]["paths"])
    assert set(_workflow("ci-quality.yml")["on"]["push"]["paths-ignore"]) == native_patterns
    native_paths = {path[:-1] if path.endswith("/**") else path for path in native_patterns}
    assert shell_paths == native_paths


@dataclass(frozen=True, kw_only=True)
class _QualityHistory:
    base: str
    head: str


def _quality_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paths: list[str]
) -> _QualityHistory:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    command_output(["git", "init", "--quiet"])
    command_output(["git", "config", "user.name", "Quality fixture"])
    command_output(["git", "config", "user.email", "quality@example.test"])
    (repository / "seed").write_text("seed\n")
    command_output(["git", "add", "."])
    command_output(["git", "commit", "--quiet", "-m", "base"])
    base = command_output(["git", "rev-parse", "HEAD"])
    for name in paths:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n")
    command_output(["git", "add", "."])
    command_output(["git", "commit", "--quiet", "--allow-empty", "-m", "head"])
    return _QualityHistory(base=base, head=command_output(["git", "rev-parse", "HEAD"]))


@pytest.mark.parametrize(
    ("paths", "run_quality"),
    [
        (["backend/app/nested/module.py"], False),
        (["backend/tests/test_module.py"], True),
        (["backend/app/nested/module.py", "backend/tests/test_module.py"], False),
        (["frontend/src/page.tsx", "scripts/shared-helper.py"], False),
        (["scripts/shared-helper.py"], True),
        (["backend/appish/module.py"], True),
        (["scripts/github/quality-ci-policy.sh"], True),
        (["backend/app/space and\nnewline.py"], False),
        (["scripts/shared\nbackend/app/fake.py"], True),
        ([], True),
        (["backend/app/module.py", *(f"tests/{i}.py" for i in range(299))], False),
        (["backend/app/module.py", *(f"tests/{i}.py" for i in range(300))], True),
    ],
)
def test_quality_routes_real_push_diffs(
    *, paths: list[str], run_quality: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _quality_history(tmp_path, monkeypatch, paths)
    output = command_output(
        [
            "bash",
            str(ROOT / "scripts/github/quality-ci-policy.sh"),
            "--diff",
            history.base,
            history.head,
        ]
    )
    assert output == f"run_quality={str(run_quality).lower()}"
    assert list(tmp_path.glob("quality-paths.*")) == []


@pytest.mark.parametrize("base", ["", "0" * 40, "1" * 40, "not-a-sha"])
def test_quality_retains_checks_for_missing_or_invalid_base(
    base: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _quality_history(tmp_path, monkeypatch, ["backend/app/module.py"]).head
    assert (
        command_output(
            [
                "bash",
                str(ROOT / "scripts/github/quality-ci-policy.sh"),
                "--diff",
                base,
                head,
            ]
        )
        == "run_quality=true"
    )


@pytest.mark.parametrize("destination", [None, "backend/tests/module.py", "backend/app/moved.py"])
def test_quality_retains_checks_for_deletions_and_both_rename_sides(
    destination: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _quality_history(tmp_path, monkeypatch, ["backend/app/module.py"]).head
    original = Path("backend/app/module.py")
    if destination is None:
        original.unlink()
    else:
        moved = Path(destination)
        moved.parent.mkdir(parents=True, exist_ok=True)
        original.rename(moved)
    command_output(["git", "add", "."])
    command_output(["git", "commit", "--quiet", "-m", "remove or move"])
    head = command_output(["git", "rev-parse", "HEAD"])
    assert (
        command_output(
            [
                "bash",
                str(ROOT / "scripts/github/quality-ci-policy.sh"),
                "--diff",
                base,
                head,
            ]
        )
        == "run_quality=true"
    )


def test_quality_retains_checks_when_native_diff_detection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = _quality_history(tmp_path, monkeypatch, ["backend/app/module.py"])
    real_git = command_output(["bash", "-c", "command -v git"])
    executable = tmp_path / "git"
    executable.write_text(
        '#!/usr/bin/env bash\nif [[ "$1" == diff ]]; then exit 23; fi\nexec "$REAL_GIT" "$@"\n'
    )
    executable.chmod(0o755)
    monkeypatch.setenv("REAL_GIT", real_git)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    assert (
        command_output(
            [
                "bash",
                str(ROOT / "scripts/github/quality-ci-policy.sh"),
                "--diff",
                history.base,
                history.head,
            ]
        )
        == "run_quality=true"
    )
    assert list(tmp_path.glob("quality-paths.*")) == []


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
            if step.get("run") == f"bash scripts/github/verify-release-gate.sh ready {component}"
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
    expected_prefix = "nextjs-v1-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('frontend/package-lock.json') }}-"

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
            "changes",
            "lint-and-typecheck",
            "test",
            "postgres-concurrency",
            "image",
        },
        "ci-frontend.yml": {"changes", "lint-and-typecheck", "test", "build"},
    }

    for name, needs in expected_needs.items():
        required = _workflow(name)["jobs"]["required"]
        assert required["if"].startswith("${{ always()")
        assert set(required["needs"]) == needs


_ActionsValue = str | bool | None


def _actions_expression(expression: str, values: dict[str, _ActionsValue]) -> object:
    body = expression.strip().removeprefix("${{").removesuffix("}}")
    body = body.replace("always()", "True")
    body = re.sub(r"(?<!')\btrue\b(?!')", "True", body)
    body = re.sub(r"\b(?:needs|github|inputs)(?:\.[a-z_-]+)+", lambda m: repr(values[m[0]]), body)
    body = body.replace("!=", "\0").replace("!", " not ").replace("\0", "!=")
    body = body.replace("&&", " and ").replace("||", " or ")
    return _evaluate_node(ast.parse(" ".join(body.split()), mode="eval").body)


def _evaluate_node(node: ast.expr) -> object:
    match node:
        case ast.BoolOp(op=ast.And(), values=operands):
            return all(_evaluate_node(operand) for operand in operands)
        case ast.BoolOp(op=ast.Or(), values=operands):
            return any(_evaluate_node(operand) for operand in operands)
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            return not _evaluate_node(operand)
        case ast.Compare(left=left, ops=[ast.Eq()], comparators=[right]):
            return _evaluate_node(left) == _evaluate_node(right)
        case ast.Compare(left=left, ops=[ast.NotEq()], comparators=[right]):
            return _evaluate_node(left) != _evaluate_node(right)
        case ast.Constant(value=value):
            return value
        case _:
            msg = f"Unsupported Actions expression node: {ast.dump(node)}"
            raise AssertionError(msg)


@pytest.mark.parametrize(
    ("component", "needs"),
    [
        (
            "backend",
            ("changes", "lint-and-typecheck", "test", "postgres-concurrency", "image"),
        ),
        ("frontend", ("changes", "lint-and-typecheck", "test", "build")),
    ],
)
def test_required_ci_aggregators_skip_only_shapes_the_gate_accepts(
    component: str, needs: tuple[str, ...]
) -> None:
    required = _workflow(f"ci-{component}.yml")["jobs"]["required"]
    assert required["needs"] == list(needs)
    assert required["if"].startswith("${{ always() && !(")
    gate_env = required["steps"][2]["env"]
    result_env = {
        match[1]: key
        for key, value in gate_env.items()
        if (match := re.fullmatch(r"\$\{\{ needs\.([a-z-]+)\.result \}\}", value))
    }
    assert set(result_env) == set(needs)

    triggers = [("pull_request", None), *itertools.product(["push"], [True, False])]
    for (event, force_full), runtime, results in itertools.product(
        triggers,
        ["true", "false", ""],
        itertools.product(_JOB_RESULTS, repeat=len(needs)),
    ):
        job_results = dict(zip(needs, results, strict=True))
        values: dict[str, _ActionsValue] = {
            "github.event_name": event,
            "inputs.force_full": force_full,
            "needs.changes.outputs.runtime": runtime,
        }
        values.update((f"needs.{job}.result", result) for job, result in job_results.items())
        environment = {result_env[job]: result for job, result in job_results.items()}
        forced = _actions_expression(gate_env["FORCE_FULL"], values)
        environment["FORCE_FULL"] = "true" if forced else "false"
        environment["RUNTIME_SELECTED"] = runtime
        try:
            require_ci_gates(environment, component)
        except ReleaseBlockedError:
            accepted = False
        else:
            accepted = True
        assert _actions_expression(required["if"], values) is not accepted, values


@pytest.mark.parametrize(
    ("paths", "backend", "frontend"),
    [
        (["README.md", "docs/nested/guide.md", "terraform/main.tf"], False, False),
        (["frontend/src/app/page.tsx"], False, True),
        (["backend/tests/test_cache.py"], True, False),
        (["backend/app/main.py"], True, True),
        (["backend/uv.lock"], True, True),
        (["frontend/openapi.json"], True, True),
        pytest.param(
            ["frontend/scripts/patch-generated.ts"],
            True,
            True,
            id="typed-api-generator",
        ),
        pytest.param(
            ["frontend/scripts/patch-generated.mjs"],
            True,
            True,
            id="legacy-api-generator",
        ),
        (["frontend/src/lib/generated/types.ts"], True, True),
        ([".github/workflows/ci-backend.yml"], True, True),
        (["scripts/github/runtime-ci-policy.sh"], True, True),
        pytest.param(["scripts/wiki.py"], True, True, id="shared-typed-cli"),
        (["docs/notes.txt"], True, True),
        (["unknown\nbackend/app/main.py"], True, True),
        (["backend/tests/old.py", "frontend/src/new.ts"], True, True),
    ],
)
def test_runtime_selection_uses_complete_nul_delimited_paths(
    paths: list[str], *, backend: bool, frontend: bool
) -> None:
    output = command_output(
        [
            "bash",
            "-e",
            "-c",
            'script=$1; shift; printf "%s\\0" "$@" | bash "$script"',
            "runtime-policy-test",
            str(ROOT / "scripts/github/runtime-ci-policy.sh"),
            *paths,
        ]
    )
    assert output == f"backend={str(backend).lower()}\nfrontend={str(frontend).lower()}"


@pytest.mark.parametrize("component", ["backend", "frontend"])
@pytest.mark.parametrize("diff_status", [0, 1])
def test_runtime_diff_preserves_routing_and_fails_open_on_unavailable_diff(
    component: str,
    diff_status: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "git"
    executable.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$GIT_CALLS"\nprintf "%s\\0" "$DIFF_PATH"\nexit "$DIFF_STATUS"\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    output = tmp_path / "output"
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    for name, value in {
        "BASE_SHA": "a" * 40,
        "HEAD_SHA": "b" * 40,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
        "GIT_CALLS": str(tmp_path / "git-calls"),
        "DIFF_PATH": "backend/tests/test_example.py",
        "DIFF_STATUS": str(diff_status),
    }.items():
        monkeypatch.setenv(name, value)
    steps = _workflow(f"ci-{component}.yml")["jobs"]["changes"]["steps"]
    (selection,) = [step for step in steps if step.get("id") == "policy"]

    log = command_output(["bash", "-e", "-c", selection["run"]])

    assert output.read_text() == (
        "backend=true\nfrontend=true\n" if diff_status else "backend=true\nfrontend=false\n"
    )
    assert ("::warning::Change detection failed" in log) is bool(diff_status)
    assert (tmp_path / "git-calls").read_text() == (
        f"diff --no-ext-diff --no-textconv --no-renames --name-only -z {'a' * 40} {'b' * 40} --\n"
    )


@pytest.mark.parametrize("component", ["backend", "frontend"])
def test_runtime_required_gates_reject_failure_missing_outputs_and_unexpected_skips(
    component: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow(f"ci-{component}.yml")
    assert workflow["on"]["workflow_call"]["inputs"]["force_full"]["default"] is True
    steps = workflow["jobs"]["required"]["steps"]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[0]["with"]["persist-credentials"] is False
    assert steps[1]["uses"].startswith("astral-sh/setup-uv@")
    assert steps[1]["with"]["version-file"] == "backend/uv.toml"
    gate = steps[2]
    baseline = dict.fromkeys(gate["env"], "success")
    baseline.update(FORCE_FULL="false", ROUTING_RESULT="success", RUNTIME_SELECTED="true")
    runtime_keys = (
        ["TEST_RESULT", "POSTGRES_RESULT"]
        if component == "backend"
        else ["TEST_RESULT", "BUILD_RESULT"]
    )
    scenarios = [(baseline, True)]
    unselected = {**baseline, "RUNTIME_SELECTED": "false"}
    unselected.update(dict.fromkeys(runtime_keys, "skipped"))
    scenarios.extend(
        [
            (unselected, True),
            ({**baseline, "FORCE_FULL": "true", "ROUTING_RESULT": "skipped"}, True),
        ]
    )
    scenarios.extend(
        ({**baseline, key: result}, False)
        for key in gate["env"]
        if key not in {"FORCE_FULL", "RUNTIME_SELECTED"}
        for result in ["failure", "cancelled", "skipped", ""]
    )
    scenarios.extend(
        ({**baseline, "RUNTIME_SELECTED": value}, False)
        for value in ["", "unknown", "TRUE", "1", " true", "true "]
    )
    scenarios.extend(
        ({**baseline, "FORCE_FULL": value, "ROUTING_RESULT": "skipped"}, False)
        for value in ["", "TRUE", "1", "yes", " true", "true "]
    )
    scenarios.extend(
        ({**unselected, key: result}, False)
        for key in runtime_keys
        for result in ["success", "failure"]
    )
    scenarios.append(({**unselected, "FORCE_FULL": "true", "ROUTING_RESULT": "skipped"}, False))
    for environment, expected in scenarios:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        command = ["bash", "-e", "-c", gate["run"]]
        if expected:
            assert not command_output(command)
        else:
            with pytest.raises(ReleaseBlockedError, match="Release command failed"):
                command_output(command)
