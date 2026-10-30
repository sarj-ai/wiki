from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).parents[2]
_QUALITY_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/ci-quality.yml"
_GITLEAKS_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"


def test_pre_commit_runtime_and_cache_are_version_bound() -> None:
    workflow = _QUALITY_WORKFLOW.read_text()
    repository_job = workflow[
        workflow.index("  repository-quality:") : workflow.index("  api-contract:")
    ]

    assert "run: bash scripts/github/run-pre-commit.sh" in repository_job
    assert "enable-cache: true" in repository_job
    assert "cache-dependency-glob: backend/uv.lock" in repository_job
    assert "pre-commit-v4.7.0-" in repository_job
    assert "${{ runner.arch }}" in repository_job
    assert "backend/.python-version" in repository_job
    assert "backend/uv.toml" in repository_job
    assert "backend/uv.lock" in repository_job
    assert ".pre-commit-config.yaml" in repository_job


def test_protected_pre_commit_check_runs_every_repository_quality_gate() -> None:
    workflow = _QUALITY_WORKFLOW.read_text()
    protected_job = workflow[
        workflow.index("  repository-quality:") : workflow.index("  api-contract:")
    ]

    assert "name: pre-commit (actionlint, hygiene, sarj rules)" in protected_job
    assert "\n    if:" not in protected_job
    assert "continue-on-error" not in protected_job
    assert "scripts/standards_checks/check_standards.py --verify-python-baseline ." in protected_job
    assert 'WIKI_PYTHON_BASELINE_JOBS: "2"' in protected_job
    assert "run: bash scripts/github/run-pre-commit.sh" in protected_job
    assert workflow.count("name: pre-commit (actionlint, hygiene, sarj rules)") == 1


def test_gitleaks_archive_is_verified_before_extraction_and_execution() -> None:
    workflow = _QUALITY_WORKFLOW.read_text()
    secret_scan_job = workflow[workflow.index("  secret-scan:") :]

    download = secret_scan_job.index("--output gitleaks.tar.gz")
    checksum = secret_scan_job.index("sha256sum --check --strict")
    extraction = secret_scan_job.index("tar -xzf gitleaks.tar.gz gitleaks")
    execution = secret_scan_job.index("./gitleaks detect")

    assert _GITLEAKS_SHA256 in secret_scan_job
    assert "GITLEAKS_VERSION: 8.30.1" in secret_scan_job
    assert download < checksum < extraction < execution
    assert "curl -sSfL" not in secret_scan_job
