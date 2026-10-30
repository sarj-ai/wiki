from dataclasses import dataclass
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest
from typer.testing import CliRunner


ROOT = Path(__file__).parents[2]
GROWTH = runpy.run_path(str(ROOT / "scripts/standards_checks/check_baseline_growth.py"))


@dataclass(frozen=True)
class _StandardsProject:
    script: Path
    source: Path
    baseline: Path


def _run(script: Path, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.fixture
def standards_project(tmp_path: Path) -> _StandardsProject:
    scripts = tmp_path / "repository/scripts/standards_checks"
    scripts.mkdir(parents=True)
    script = scripts / "check_standards.py"
    script.write_bytes((ROOT / "scripts/standards_checks/check_standards.py").read_bytes())
    source = tmp_path / "repository/source"
    source.mkdir()
    (source / "target.py").write_text(
        "def collect(**kwargs: object) -> dict[str, object]:\n    return kwargs\n"
    )
    baseline = scripts.parents[1] / "python-standards-baseline.json"
    baseline.write_text("{}")
    return _StandardsProject(script=script, source=source, baseline=baseline)


def test_standards_cli_uses_native_engine_and_private_exact_baseline(
    standards_project: _StandardsProject,
) -> None:
    script = standards_project.script
    source = standards_project.source
    baseline = standards_project.baseline
    result = _run(script, "target.py", cwd=source)
    assert result.returncode == 1
    assert "SARJ477" in result.stdout
    assert json.loads(baseline.read_text()) == {}

    result = _run(script, "--update-python-baseline", "target.py", cwd=source)
    assert result.returncode == 0
    recorded = json.loads(baseline.read_text())
    assert recorded["target.py"]["SARJ477"] == 1
    assert _run(script, "--verify-python-baseline", "target.py", cwd=source).returncode == 0
    assert _run(script, cwd=source).returncode == 0

    recorded["removed.py"] = {"SARJ477": 1}
    baseline.write_text(json.dumps(recorded))
    result = _run(script, "--verify-python-baseline", "target.py", cwd=source)
    assert result.returncode == 1
    assert "stale or inflated" in result.stderr


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        pytest.param(["--help"], 0, id="help"),
        pytest.param(["--unknown"], 2, id="unknown-option"),
        pytest.param(
            ["--verify-python-baseline", "--update-python-baseline"], 2, id="conflicting-modes"
        ),
    ],
)
def test_standards_help_and_invalid_modes_leave_private_baseline_untouched(
    standards_project: _StandardsProject, arguments: list[str], expected: int
) -> None:
    script = standards_project.script
    source = standards_project.source
    baseline = standards_project.baseline
    baseline.write_text("INVALID BASELINE")
    result = _run(script, *arguments, cwd=source)
    assert result.returncode == expected
    assert "SARJ477" not in result.stdout
    assert "Traceback" not in result.stderr
    assert baseline.read_text() == "INVALID BASELINE"


def test_initial_growth_cli_preserves_empty_prefix_and_only_checks_changed_files(
    tmp_path: Path,
) -> None:
    baseline, changed = tmp_path / "baseline.json", tmp_path / "changed"
    baseline.write_text('{"existing.py": {"rule": 1}}')
    changed.write_text("other.py\n")
    arguments = ["--initial", str(baseline), str(changed), ""]

    assert CliRunner().invoke(GROWTH["CLI"], arguments).exit_code == 0
    changed.write_text("existing.py\n")
    result = CliRunner().invoke(GROWTH["CLI"], arguments)

    assert result.exit_code == 1
    assert "cannot exempt files changed" in result.stderr


def test_growth_cli_keeps_per_file_shrink_only_behavior(tmp_path: Path) -> None:
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text('{"existing.py": {"rule": 2}}')
    after.write_text('{"existing.py": {"rule": 1}}')

    assert CliRunner().invoke(GROWTH["CLI"], [str(before), str(after)]).exit_code == 0
    after.write_text('{"relocated.py": {"rule": 1}}')
    result = CliRunner().invoke(GROWTH["CLI"], [str(before), str(after)])

    assert result.exit_code == 1
    assert "relocated.py/rule: 0 -> 1" in result.stderr
