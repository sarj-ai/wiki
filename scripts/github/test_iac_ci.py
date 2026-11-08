import json
import os
from pathlib import Path
import sys
import tomllib

import pytest
import yaml

from scripts.github.release_gate import ReleaseBlockedError, command_output
from scripts.wiki import REPOSITORY_STANDARDS_VERSION


ROOT = Path(__file__).resolve().parents[2]
_CACHE_ACTION = "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
_PLATFORM_PREFIX = "terraform-providers-${{ runner.os }}-${{ runner.arch }}-"


def test_shared_release_preserves_all_qualification_and_pins_original_repository_id() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-main.yml").read_text())
    jobs = workflow["jobs"]

    assert jobs["standards-receipt"]["if"] == "github.repository_id == '1183627122'"
    for name in ["ci-backend", "ci-frontend", "ci-quality", "ci-iac"]:
        assert "if" not in jobs[name]
        assert jobs[name]["uses"] == f"./.github/workflows/{name}.yml"


def test_cold_quality_checks_initialize_locked_local_providers_after_tool_setup() -> None:
    initialize = "terraform -chdir=terraform init -backend=false -input=false -lockfile=readonly"
    standards_initialize = (
        f"mise --no-config --no-env --no-hooks exec aqua:hashicorp/terraform@1.17.0 -- {initialize}"
    )
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
    pre_commit = next(step for step in quality_steps if step.get("name") == "Run pre-commit")
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
        step for step in standards_steps if step.get("run", "").strip() == standards_initialize
    )
    check = next(step for step in standards_steps if step.get("name") == "Run standards")
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
        step for step in job["steps"] if step.get("uses", "").startswith("actions/cache@")
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
        if step.get("run") == "uv run scripts/wiki.py check iac"
    )
    assert "if" not in steps[terraform_index]
    trivy = next(
        step for step in steps if step.get("uses", "").startswith("aquasecurity/trivy-action@")
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
    footer = next(step for step in steps if step.get("name") == "Close IaC scan summary")
    upload = next(step for step in steps if step.get("name") == "Upload IaC scan summary")
    assert header["if"] == "always()"
    assert report["if"] == "always() && steps.trivy_summary_header.outcome == 'success'"
    assert footer["if"] == (
        "always() && steps.trivy_summary_header.outcome == 'success' && steps.trivy_summary_report.outcome == 'success'"
    )
    assert upload["if"] == (
        "always() && steps.trivy_summary_header.outcome == 'success' && steps.trivy_summary_report.outcome == 'success' && steps.trivy_summary_footer.outcome == 'success'"
    )
    summary = tmp_path / "summary"
    prepared = tmp_path / "trivy-summary.md"
    if scenario == "failed-header":
        prepared.mkdir()
    if scenario == "report":
        (tmp_path / "trivy-report.txt").write_text("finding\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "header-summary"))
    if scenario == "failed-header":
        with pytest.raises(ReleaseBlockedError):
            command_output(["bash", "-e", "-c", header["run"]])
        assert prepared.is_dir()
        assert not summary.exists()
        return
    command_output(["bash", "-e", "-c", header["run"]])
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "report-summary"))
    if scenario == "missing-report":
        with pytest.raises(ReleaseBlockedError):
            command_output(["bash", "-e", "-c", report["run"]])
        assert prepared.read_text() == "## Trivy IaC scan (terraform/)\n```\n"
        assert not summary.exists()
        return
    command_output(["bash", "-e", "-c", report["run"]])
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "footer-summary"))
    command_output(["bash", "-e", "-c", footer["run"]])
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    command_output(["bash", "-e", "-c", upload["run"]])
    assert list(tmp_path.glob("*-summary")) == []
    assert summary.read_text() == "## Trivy IaC scan (terraform/)\n```\nfinding\n```\n"


@pytest.mark.parametrize("scenario", ["success", "failed-analysis"])
def test_pull_request_size_executes_event_refs_report_and_fail_fast(
    scenario: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/pr-size-check.yml").read_text())
    measure = next(
        step
        for step in workflow["jobs"]["size"]["steps"]
        if step.get("name") == "Measure pull-request size"
    )
    summaries = [
        step
        for step in workflow["jobs"]["size"]["steps"]
        if step.get("name", "").endswith(" size summary")
    ]
    assert [step["name"] for step in summaries] == [
        "Open pull-request size summary",
        "Write pull-request size summary",
        "Close pull-request size summary",
        "Publish pull-request size summary",
    ]
    assert all("if" not in step and "continue-on-error" not in step for step in summaries)
    assert measure["env"] == {
        "REPOSITORY_STANDARDS_VERSION": REPOSITORY_STANDARDS_VERSION,
        "PR_BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "PR_HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
    calls_path = tmp_path / "uvx-calls.jsonl"
    summary_path = tmp_path / "step-summary.txt"
    uvx = tmp_path / "uvx"
    uvx.write_text(
        """#!PYTHON_EXECUTABLE
import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
with Path(os.environ["WIKI_PR_SIZE_CALLS"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")
prefix = [
    "--no-config", "--isolated", "--python", "3.15.0", "--from",
    "repo-standards==" + os.environ["REPOSITORY_STANDARDS_VERSION"],
]
assert arguments[:len(prefix)] == prefix
command = arguments[len(prefix):]
if command[:4] == ["repo-standards", "pull-request", "size", "."]:
    if os.environ["WIKI_PR_SIZE_SCENARIO"] == "failed-analysis":
        print("Partial native output from failed analysis")
        raise SystemExit(23)
    report = Path(command[command.index("--report-path") + 1])
    report.write_text(json.dumps({"summary": {"counted_lines": 7, "excluded_lines": 11, "total_lines": 18}}), encoding="utf-8")
    print("Counted review size: 7 lines (+5/-2, 1 files)")
    print("Excluded churn: 11 lines (+8/-3, 2 files)")
    print("Total churn: 18 lines (+13/-5, 3 files)")
else:
    raise AssertionError("Unexpected uvx process boundary")
""".replace("PYTHON_EXECUTABLE", sys.executable),
        encoding="utf-8",
    )
    uvx.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    monkeypatch.setenv("REPOSITORY_STANDARDS_VERSION", REPOSITORY_STANDARDS_VERSION)
    monkeypatch.setenv("PR_BASE_SHA", "b" * 40)
    monkeypatch.setenv("PR_HEAD_SHA", "c" * 40)
    monkeypatch.setenv("WIKI_PR_SIZE_CALLS", str(calls_path))
    monkeypatch.setenv("WIKI_PR_SIZE_SCENARIO", scenario)

    if scenario == "failed-analysis":
        with pytest.raises(ReleaseBlockedError, match="Release command failed"):
            command_output(["bash", "-e", "-c", measure["run"]])
    else:
        assert not command_output(["bash", "-e", "-c", measure["run"]])
        for index, step in enumerate(summaries):
            monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / f"step-summary-{index}"))
            assert not command_output(["bash", "-e", "-c", step["run"]])
        summary_path = tmp_path / "step-summary-3"
        assert list(tmp_path.glob("step-summary-*")) == [summary_path]

    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    prefix = [
        "--no-config",
        "--isolated",
        "--python",
        "3.15.0",
        "--from",
        "repo-standards==" + measure["env"]["REPOSITORY_STANDARDS_VERSION"],
    ]
    report_path = Path(calls[0][calls[0].index("--report-path") + 1])
    assert report_path.parent == tmp_path
    assert report_path.name == "repo-standards-pr-size.json"
    assert calls[0] == [
        *prefix,
        "repo-standards",
        "pull-request",
        "size",
        ".",
        "--base",
        "b" * 40,
        "--head",
        "c" * 40,
        "--generated-attribute",
        "pr-size-excluded",
        "--report-path",
        str(report_path),
        "--format",
        "text",
    ]
    if scenario == "failed-analysis":
        assert len(calls) == 1
        assert not report_path.exists()
        assert report_path.with_suffix(".txt").read_text(encoding="utf-8") == (
            "Partial native output from failed analysis\n"
        )
        assert not summary_path.exists()
        return
    assert calls == [calls[0]]
    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        "summary": {"counted_lines": 7, "excluded_lines": 11, "total_lines": 18}
    }
    assert summary_path.read_text(encoding="utf-8") == (
        "## Pull-request size\n\n```text\n"
        "Counted review size: 7 lines (+5/-2, 1 files)\n"
        "Excluded churn: 11 lines (+8/-3, 2 files)\n"
        "Total churn: 18 lines (+13/-5, 3 files)\n"
        "```\n"
    )


@pytest.mark.parametrize(
    ("workflow", "job_name"),
    [
        ("standards", "standards"),
        ("ci-frontend", "lint-and-typecheck"),
        ("ci-quality", "repository-quality"),
    ],
)
def test_root_lint_ci_has_complete_comparison_history(workflow: str, job_name: str) -> None:
    document = yaml.safe_load((ROOT / f".github/workflows/{workflow}.yml").read_text())
    steps = document["jobs"][job_name]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert "sparse-checkout" not in checkout["with"]


@pytest.mark.parametrize(
    ("workflow", "job_name", "gate_name"),
    [
        ("standards", "standards", "Run standards"),
        ("ci-frontend", "lint-and-typecheck", "Native lint with exact baseline"),
    ],
)
def test_direct_root_lint_ci_attests_native_tools_before_analysis(
    workflow: str, job_name: str, gate_name: str
) -> None:
    document = yaml.safe_load((ROOT / f".github/workflows/{workflow}.yml").read_text())
    steps = document["jobs"][job_name]["steps"]
    bootstrap = next(
        step for step in steps if step.get("name") == "Install the pinned native tool bootstrap"
    )
    setup = next(
        step for step in steps if step.get("name") == "Install and attest applicable native tools"
    )
    gate = next(step for step in steps if step.get("name") == gate_name)
    assert "--with code-standards==9.2.3" in bootstrap["run"]
    assert bootstrap["run"].endswith("python -m sarj_standards.libs.adoption.native_bootstrap")
    assert setup["run"].endswith("code-standards setup --tools-only")
    assert steps.index(bootstrap) < steps.index(setup) < steps.index(gate)
    for step in [bootstrap, setup, gate]:
        assert "if" not in step
        assert "continue-on-error" not in step
    for step in [bootstrap, setup]:
        assert step.get("working-directory", ".") == "."
    comparison = gate["env"]["SARJ_STANDARDS_BASE"]
    for base in [
        "github.event.pull_request.base.sha",
        "github.event.merge_group.base_sha",
        "github.event.before",
    ]:
        assert base in comparison
    uv_setup = next(
        step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert uv_setup["with"]["version-file"] == "backend/uv.toml"
    assert steps.index(uv_setup) < steps.index(bootstrap)
    if workflow == "ci-frontend":
        assert gate["run"] == "npm run lint"
        package = json.loads((ROOT / "package.json").read_text())
        command = package["scripts"]["lint"]
    else:
        command = gate["run"]
    assert "--from sarj-standards-bootstrap" in command
    assert "--trust-repository-code" in command


@pytest.mark.parametrize("workflow", ["ci-iac", "ci-quality"])
def test_ci_terraform_matches_repository_pin(workflow: str) -> None:
    manifest = tomllib.loads((ROOT / ".mise.toml").read_text())
    document = yaml.safe_load((ROOT / f".github/workflows/{workflow}.yml").read_text())
    job = "required" if workflow == "ci-iac" else "repository-quality"
    setup = next(
        step
        for step in document["jobs"][job]["steps"]
        if step.get("uses", "").startswith("hashicorp/setup-terraform@")
    )
    assert setup["with"]["terraform_version"] == manifest["tools"]["terraform"]
    assert setup["with"]["terraform_wrapper"] is False
