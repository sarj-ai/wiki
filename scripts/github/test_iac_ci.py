from pathlib import Path
import tomllib

import pytest
from scripts.github.release_gate import ReleaseBlockedError, command_output
import yaml


ROOT = Path(__file__).resolve().parents[2]
_CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_PLATFORM_PREFIX = "terraform-providers-${{ runner.os }}-${{ runner.arch }}-"


def test_cold_quality_checks_initialize_locked_local_providers_after_tool_setup() -> (
    None
):
    initialize = (
        "terraform -chdir=terraform init -backend=false -input=false -lockfile=readonly"
    )
    standards_initialize = f"mise --no-config --no-env --no-hooks exec aqua:hashicorp/terraform@1.15.8 -- {initialize}"
    manifest = tomllib.loads((ROOT / ".sarj-standards.toml").read_text())
    assert manifest["ci"]["bootstrap"] == [
        "npm --prefix frontend run patches:apply",
        "npm --prefix frontend run generate:api",
        "npm --prefix frontend run typegen",
        standards_initialize,
    ]
    iac = yaml.safe_load((ROOT / ".github/workflows/ci-iac.yml").read_text())
    provider_setup = next(
        step
        for step in iac["jobs"]["required"]["steps"]
        if step.get("uses", "").startswith("hashicorp/setup-terraform@")
    )
    quality = yaml.safe_load((ROOT / ".github/workflows/ci-quality.yml").read_text())
    quality_steps = quality["jobs"]["repository-quality"]["steps"]
    quality_init = next(step for step in quality_steps if step.get("run") == initialize)
    pre_commit = next(
        step for step in quality_steps if step.get("name") == "Run pre-commit"
    )
    assert quality_steps.index(provider_setup) < quality_steps.index(quality_init)
    assert quality_steps.index(quality_init) < quality_steps.index(pre_commit)

    standards = yaml.safe_load((ROOT / ".github/workflows/standards.yml").read_text())
    standards_steps = standards["jobs"]["standards"]["steps"]
    tools = next(
        step
        for step in standards_steps
        if step.get("run", "").endswith("code-standards setup --tools-only")
    )
    standards_init = next(
        step
        for step in standards_steps
        if step.get("run", "").strip() == standards_initialize
    )
    check = next(
        step for step in standards_steps if step.get("name") == "Run standards"
    )
    assert standards_steps.index(tools) < standards_steps.index(standards_init)
    assert standards_steps.index(standards_init) < standards_steps.index(check)
    for step in [quality_init, standards_init]:
        assert "if" not in step
        assert "continue-on-error" not in step
        assert "working-directory" not in step
        assert "TF_DATA_DIR" not in step.get("env", {})


def test_iac_validation_uses_locked_provider_cache_without_weakening_gates() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-iac.yml").read_text())
    jobs = workflow["jobs"]
    job = jobs["required"]
    cache_directory = job["env"]["TF_PLUGIN_CACHE_DIR"]
    assert Path(cache_directory).is_absolute()
    assert cache_directory.endswith("/terraform-provider-cache")

    (cache_step,) = [
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/cache@")
    ]
    assert cache_step["uses"] == _CACHE_ACTION
    prepare_index = next(
        index
        for index, step in enumerate(job["steps"])
        if step.get("name") == "Prepare Terraform provider cache"
    )
    cache_index = job["steps"].index(cache_step)
    assert prepare_index < cache_index
    assert cache_step["with"]["path"] == "${{ env.TF_PLUGIN_CACHE_DIR }}"
    assert cache_step["with"]["key"] == (
        _PLATFORM_PREFIX + "${{ hashFiles('terraform/.terraform.lock.hcl') }}"
    )
    assert cache_step["with"]["restore-keys"] == f"{_PLATFORM_PREFIX}\n"

    assert list(jobs) == ["required"]
    assert job["name"] == "IaC / required"
    assert "if" not in job
    assert "continue-on-error" not in job
    steps = job["steps"]
    assert all("continue-on-error" not in step for step in steps)
    terraform_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "make check-iac"
    )
    assert "if" not in steps[terraform_index]
    trivy = next(
        step
        for step in steps
        if step.get("uses", "").startswith("aquasecurity/trivy-action@")
    )
    assert terraform_index < steps.index(trivy)
    assert trivy["if"] == "${{ !cancelled() }}"
    assert trivy["with"]["exit-code"] == "1"
    assert trivy["with"]["severity"] == "HIGH,CRITICAL"


@pytest.mark.parametrize("scenario", ["report", "missing-report", "failed-header"])
def test_iac_summary_preserves_content_and_stops_after_a_failed_write(
    scenario: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-iac.yml").read_text())
    steps = workflow["jobs"]["required"]["steps"]
    header = next(step for step in steps if step.get("id") == "trivy_summary_header")
    report = next(step for step in steps if step.get("id") == "trivy_summary_report")
    footer = next(
        step for step in steps if step.get("name") == "Close IaC scan summary"
    )
    assert header["if"] == "always()"
    assert report["if"] == "always() && steps.trivy_summary_header.outcome == 'success'"
    assert footer["if"] == (
        "always() && steps.trivy_summary_header.outcome == 'success' && steps.trivy_summary_report.outcome == 'success'"
    )
    summary = tmp_path / "summary"
    if scenario == "failed-header":
        summary.mkdir()
    if scenario == "report":
        (tmp_path / "trivy-report.txt").write_text("finding\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    if scenario == "failed-header":
        with pytest.raises(ReleaseBlockedError):
            command_output(["bash", "-e", "-c", header["run"]])
        assert summary.is_dir()
        return
    command_output(["bash", "-e", "-c", header["run"]])
    if scenario == "missing-report":
        with pytest.raises(ReleaseBlockedError):
            command_output(["bash", "-e", "-c", report["run"]])
        assert summary.read_text() == "## Trivy IaC scan (terraform/)\n```\n"
        return
    command_output(["bash", "-e", "-c", report["run"]])
    command_output(["bash", "-e", "-c", footer["run"]])
    assert summary.read_text() == "## Trivy IaC scan (terraform/)\n```\nfinding\n```\n"
