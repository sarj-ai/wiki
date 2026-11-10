from pathlib import Path


_DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
_REPOSITORY_ROOT = Path(__file__).parents[2]
_DEPLOY_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/deploy-backend.yml"
_DEPLOY_CANDIDATE = _REPOSITORY_ROOT / "scripts/github/deploy-backend-candidate.sh"
_RUN_MIGRATIONS = _REPOSITORY_ROOT / "scripts/github/run-backend-migrations.sh"
_LEGACY_CACHE_CLEANUP = _REPOSITORY_ROOT / "scripts/github/clear-legacy-drive-tree-cache.sh"
_LEGACY_CACHE_CLEANUP_MODULE = _REPOSITORY_ROOT / "backend/app/cache/cleanup_legacy.py"
_VERIFY_LOGS = _REPOSITORY_ROOT / "scripts/github/verify-backend-correlated-logs.sh"
_MIGRATIONS_TERRAFORM = _REPOSITORY_ROOT / "terraform/migrations.tf"
_BACKEND_CI = _REPOSITORY_ROOT / ".github/workflows/ci-backend.yml"


def test_application_container_does_not_run_database_migrations() -> None:
    dockerfile = _DOCKERFILE.read_text()
    assert "alembic upgrade" not in dockerfile
    assert (
        'CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]'
        in dockerfile
    )


def test_deployment_runs_one_shot_migrations_before_traffic_switch() -> None:
    workflow = _DEPLOY_WORKFLOW.read_text()
    candidate = workflow.index("- name: Deploy candidate without traffic")
    migration = workflow.index("- name: Run database migrations once")
    traffic = workflow.index("- name: Switch production traffic")

    assert candidate < migration < traffic
    assert "scripts/github/deploy-backend-candidate.sh" in workflow[candidate:migration]
    assert "scripts/github/run-backend-migrations.sh" in workflow[migration:traffic]
    assert "steps.deploy.outputs.release_image }}" in workflow[migration:traffic]

    candidate_script = _DEPLOY_CANDIDATE.read_text()
    assert "--no-traffic" in candidate_script
    assert 'select(.type == "Ready")' in candidate_script
    assert "latestReadyRevisionName" not in candidate_script

    migration_script = _RUN_MIGRATIONS.read_text()
    assert "--wait" in migration_script
    assert '"$RELEASE_IMAGE"' in migration_script


def test_deployment_does_not_reconfigure_job_for_retired_legacy_cache() -> None:
    workflow = _DEPLOY_WORKFLOW.read_text()

    assert "Clear legacy global drive-tree cache" not in workflow
    assert "clear-legacy-drive-tree-cache.sh" not in workflow
    assert not _LEGACY_CACHE_CLEANUP.exists()
    assert not _LEGACY_CACHE_CLEANUP_MODULE.exists()


def test_migration_job_has_database_only_environment() -> None:
    terraform = _MIGRATIONS_TERRAFORM.read_text()
    start = terraform.index('resource "google_cloud_run_v2_job" "backend_migrations_dammam"')
    end = terraform.index(
        'resource "google_cloud_run_v2_job_iam_member"',
        start,
    )
    migration_job = terraform[start:end]
    assert "SECRET_KEY" not in migration_job
    assert "FERNET_KEY" not in migration_job
    assert "GOOGLE_CLIENT" not in migration_job


def test_log_gate_allows_for_cloud_logging_ingestion_delay() -> None:
    workflow = _DEPLOY_WORKFLOW.read_text()
    log_gate = workflow.index("- name: Verify correlated production logs")
    blocked_release = workflow.index("- name: Report blocked release without rollback")

    assert "scripts/github/verify-backend-correlated-logs.sh" in workflow[log_gate:blocked_release]
    assert "for _attempt in {1..12}; do" in _VERIFY_LOGS.read_text()
