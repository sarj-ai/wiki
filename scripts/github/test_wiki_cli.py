from hashlib import sha256
from pathlib import Path
import sys

from pydantic import BaseModel
import pytest
import typer
from typer.testing import CliRunner

from scripts import wiki
from scripts.standards_checks.check_baseline_growth import (
    _BootstrapGrowth,
    _validated_allowances,
)


_USAGE_ERROR = 2
_NATIVE_FAILURE = 7


class _Invocation(BaseModel):
    arguments: list[str]
    setting: str | None
    terraform_data_directory: str | None


@pytest.fixture
def native_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trace = tmp_path / "commands.jsonl"
    executable = tmp_path / "fixture-tool"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"with pathlib.Path({str(trace)!r}).open('a') as trace:\n"
        "    trace.write(json.dumps({'arguments':sys.argv[1:],"
        "'setting':os.getenv('WIKI_TEST_SETTING'),'terraform_data_directory':os.getenv('TF_DATA_DIR')})+'\\n')\n"
        f"raise SystemExit({_NATIVE_FAILURE} if 'fail' in sys.argv else 0)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    return trace


def _invocations(trace: Path) -> list[_Invocation]:
    return [
        _Invocation.model_validate_json(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
    ]


def test_help_requires_no_application_or_native_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    result = CliRunner().invoke(wiki.CLI, ["--help"])

    assert result.exit_code == 0
    assert "check" in result.stdout
    assert "standards" in result.stdout
    assert "setup" in result.stdout


@pytest.mark.parametrize(
    "forwarded",
    [
        [],
        ["frontend"],
        ["--staged", "--format", "json", "frontend/path with spaces.ts"],
        ["frontend/path with spaces.ts", "--format=json", "--unknown-option"],
        ["fail"],
    ],
    ids=["root", "frontend", "flags-before-path", "flags-after-path", "nonzero"],
)
def test_lint_launches_original_bootstrap_with_exact_forwarded_arguments(
    native_tool: Path, forwarded: list[str]
) -> None:
    executable = native_tool.parent / "fixture-tool"
    executable.rename(native_tool.parent / "uvx")

    result = CliRunner().invoke(wiki.CLI, ["lint", *forwarded])

    assert result.exit_code == (_NATIVE_FAILURE if "fail" in forwarded else 0)
    (call,) = _invocations(native_tool)
    assert call.arguments == [
        "--no-config",
        "--isolated",
        "--python",
        "3.14",
        "--from",
        "sarj-standards-bootstrap",
        "code-standards",
        "check",
        "--trust-repository-code",
        *forwarded,
    ]


def test_unknown_check_scope_fails_before_starting_a_tool(native_tool: Path) -> None:
    result = CliRunner().invoke(wiki.CLI, ["check", "unknown"])

    assert result.exit_code == _USAGE_ERROR
    assert not native_tool.exists()


def test_failed_native_command_preserves_exit_code_and_stops_later_steps(
    native_tool: Path,
) -> None:
    commands = tuple(
        wiki.Command(arguments=("fixture-tool", argument))
        for argument in ("first", "fail", "unreachable")
    )

    with pytest.raises(typer.Exit) as failed:
        wiki.run(commands)

    assert failed.value.exit_code == _NATIVE_FAILURE
    assert [call.arguments for call in _invocations(native_tool)] == [
        ["first"],
        ["fail"],
    ]


def test_command_environment_does_not_leak_to_the_next_tool(
    native_tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIKI_TEST_SETTING", "outer")
    wiki.run(
        (
            wiki.Command(
                arguments=("fixture-tool", "custom"),
                environment=(("WIKI_TEST_SETTING", "inner"),),
            ),
            wiki.Command(arguments=("fixture-tool", "inherited")),
        )
    )

    assert [call.setting for call in _invocations(native_tool)] == ["inner", "outer"]


def test_quality_suppression_failure_stops_before_other_gates(native_tool: Path) -> None:
    executable = native_tool.parent / "fixture-tool"
    for name in ("uv", "uvx"):
        target = native_tool.parent / name
        target.write_text(
            executable.read_text().replace("'fail' in sys.argv", "'sarj-ratchet' in sys.argv")
        )
        target.chmod(0o700)

    result = CliRunner().invoke(wiki.CLI, ["check", "quality"])

    assert result.exit_code == _NATIVE_FAILURE
    (call,) = _invocations(native_tool)
    assert call.arguments == [
        "run",
        "--project",
        "backend",
        "--locked",
        "sarj-ratchet",
        ".",
    ]


@pytest.mark.parametrize(
    ("scopes", "expected_count"),
    [(["backend", "frontend"], 18), (["backend", "backend"], 9)],
    ids=["ordered-subsystems", "duplicate-subsystem-runs-once"],
)
def test_multiple_scopes_preserve_order_without_repeating_native_commands(
    native_tool: Path, scopes: list[str], expected_count: int
) -> None:
    executable = native_tool.parent / "fixture-tool"
    for name in ["uv", "npm"]:
        target = native_tool.parent / name
        target.write_bytes(executable.read_bytes())
        target.chmod(0o700)

    result = CliRunner().invoke(wiki.CLI, ["check", *scopes])

    assert result.exit_code == 0
    calls = _invocations(native_tool)
    assert len(calls) == expected_count
    assert calls[0].arguments == [
        "--directory",
        "backend",
        "run",
        "--locked",
        "deptry",
        ".",
    ]
    if "frontend" in scopes:
        assert calls[-1].arguments == ["--prefix", "frontend", "run", "build"]


def test_backend_contract_keeps_all_type_architecture_and_coverage_gates() -> None:
    commands = wiki.checks(wiki.Check.BACKEND)
    native = [command.arguments[5:] for command in commands]

    assert native == [
        ("deptry", "."),
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
        ("mypy", "app"),
        ("basedpyright", "-p", "pyrightconfig.json"),
        ("ty", "check", "app"),
        ("vulture",),
        ("lint-imports",),
        ("pytest", "--cov=app", "--cov-report=term-missing", "--cov-fail-under=70"),
    ]
    assert all(
        command.arguments[:5] == ("uv", "--directory", "backend", "run", "--locked")
        for command in commands
    )


def test_frontend_contract_checks_generated_api_and_types_before_the_production_build() -> None:
    commands = wiki.checks(wiki.Check.FRONTEND)

    assert commands[0].arguments[-1] == "generate:api"
    scripts = [command.arguments[-1] for command in commands]
    assert scripts == [
        "generate:api",
        "format:check",
        "typecheck",
        "lint",
        "check:dependencies",
        "knip",
        "type-coverage",
        "test",
        "build",
    ]
    assert commands[-1].environment == (("NEXT_PUBLIC_API_URL", "http://localhost:8000"),)


def test_full_qualification_keeps_one_repository_audit_and_required_gates() -> None:
    commands = wiki.checks(wiki.Check.ALL)
    ratchet = wiki.Command(
        arguments=("uv", "run", "--project", "backend", "--locked", "sarj-ratchet", ".")
    )

    assert commands[1] == ratchet
    assert commands.count(ratchet) == 1
    assert wiki.REPOSITORY_STANDARDS_VERSION == "6.22.3"
    distribution = f"repo-standards=={wiki.REPOSITORY_STANDARDS_VERSION}"
    assert sum(distribution in command.arguments for command in commands) == 1
    assert any("--verify-python-baseline" in command.arguments for command in commands)
    assert any(
        "generate_sarj_loader.py" in argument
        for command in commands
        for argument in command.arguments
    )
    assert commands[-1].arguments == ("bash", "scripts/check-terraform.sh")


def test_macos_serializes_quality_engines_without_changing_checks() -> None:
    linux = wiki.checks(wiki.Check.QUALITY, platform="linux")
    macos = wiki.checks(wiki.Check.QUALITY, platform="darwin")

    changes = [
        (before, after) for before, after in zip(linux, macos, strict=True) if before != after
    ]
    assert len(changes) == 1
    before, after = changes[0]
    assert before.environment == after.environment
    jobs = before.arguments.index("--jobs") + 1
    assert before.arguments[jobs] == "2"
    assert after.arguments[jobs] == "1"
    assert before.arguments[:jobs] == after.arguments[:jobs]
    assert before.arguments[jobs + 1 :] == after.arguments[jobs + 1 :]
    assert after.arguments[jobs + 1 :] == (
        "--trust-repository-code",
        "--skip-python-type-check",
        ".",
    )


def test_explicit_branch_policy_arguments_reach_the_original_guard(
    native_tool: Path,
) -> None:
    executable = native_tool.parent / "fixture-tool"
    executable.rename(native_tool.parent / "uv")

    result = CliRunner().invoke(
        wiki.CLI,
        ["branch-protection", "apply", "--reviewed-fingerprint", "fixture-fingerprint"],
    )

    assert result.exit_code == 0
    (call,) = _invocations(native_tool)
    assert call.arguments[-4:] == [
        "scripts/github/branch_protection.py",
        "apply",
        "--reviewed-fingerprint",
        "fixture-fingerprint",
    ]


def test_setup_initializes_locked_local_providers_after_packages_and_preserves_data_directory(
    native_tool: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = native_tool.parent / "fixture-tool"
    for name in ["sh", "uvx", "uv", "npm", "terraform"]:
        target = native_tool.parent / name
        target.write_bytes(executable.read_bytes())
        target.chmod(0o700)
    selected_directory = str(native_tool.parent / "operator-selected-terraform-data")
    monkeypatch.setenv("TF_DATA_DIR", selected_directory)

    result = CliRunner().invoke(wiki.CLI, ["setup"])

    assert result.exit_code == 0
    calls = _invocations(native_tool)
    assert calls[0].arguments == ["scripts/github/install-hooks.sh"]
    assert calls[1].arguments[-4:] == [
        "code-standards",
        "update",
        "--offline",
        "--no-install",
    ]
    assert [call.arguments for call in calls[2:]] == [
        ["sync", "--project", "backend", "--locked"],
        ["ci", "--prefix", "frontend"],
        [
            "-chdir=terraform",
            "init",
            "-backend=false",
            "-input=false",
            "-lockfile=readonly",
        ],
    ]
    assert all(call.terraform_data_directory == selected_directory for call in calls)


@pytest.mark.parametrize("review", ["missing", "matching", "tampered"])
def test_migration_allowance_requires_the_exact_reviewed_changed_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, review: str
) -> None:
    monkeypatch.chdir(tmp_path)
    source = Path("frontend/sample.ts")
    source.parent.mkdir()
    source.write_text("export const value = 1;\n", encoding="utf-8")
    digest = sha256(source.read_bytes()).hexdigest()
    hashes = {} if review == "missing" else {str(source): digest}
    if review == "tampered":
        source.write_text("export const value = 2;\n", encoding="utf-8")

    allowances = {"sample.ts": {"rule": _BootstrapGrowth(before=0, after=1)}}

    def verify() -> bool:
        return bool(
            _validated_allowances(
                allowances,
                bootstrap="review.json",
                changed={str(source)},
                changed_prefix="frontend/",
                reviewed_source_hashes=hashes,
            )
        )

    if review == "matching":
        assert verify()
    else:
        with pytest.raises(SystemExit) as failure:
            verify()
        assert failure.value.code == _USAGE_ERROR
