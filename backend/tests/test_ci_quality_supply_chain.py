from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).parents[2]
_QUALITY_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/ci-quality.yml"
_GITLEAKS_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"


def test_pre_commit_runtime_and_cache_are_version_bound() -> None:
    workflow = _QUALITY_WORKFLOW.read_text()
    pre_commit_job = workflow[workflow.index("  pre-commit:") : workflow.index("  api-contract:")]

    assert pre_commit_job.count("uvx --python 3.14.7 'pre-commit==4.6.2'") == 2
    assert "enable-cache: true" in pre_commit_job
    assert "cache-dependency-glob: backend/uv.lock" in pre_commit_job
    assert "pre-commit-v4.6.2-" in pre_commit_job
    assert "${{ runner.arch }}" in pre_commit_job
    assert "backend/.python-version" in pre_commit_job
    assert "backend/uv.toml" in pre_commit_job
    assert "backend/uv.lock" in pre_commit_job
    assert ".pre-commit-config.yaml" in pre_commit_job


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
