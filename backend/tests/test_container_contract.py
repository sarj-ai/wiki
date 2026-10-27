from pathlib import Path


_DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
_QA_DOCKERFILE = Path(__file__).parents[1] / "Dockerfile.qa"
_REPOSITORY_ROOT = Path(__file__).parents[2]
_DEPLOY_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/deploy-backend.yml"
_DEPLOY_CANDIDATE = _REPOSITORY_ROOT / "scripts/github/deploy-backend-candidate.sh"
_RUN_MIGRATIONS = _REPOSITORY_ROOT / "scripts/github/run-backend-migrations.sh"
_LEGACY_CACHE_CLEANUP = _REPOSITORY_ROOT / "scripts/github/clear-legacy-drive-tree-cache.sh"
_LEGACY_CACHE_CLEANUP_MODULE = _REPOSITORY_ROOT / "backend/app/cache/cleanup_legacy.py"
_VERIFY_LOGS = _REPOSITORY_ROOT / "scripts/github/verify-backend-correlated-logs.sh"
_MIGRATIONS_TERRAFORM = _REPOSITORY_ROOT / "terraform/migrations.tf"
_BACKEND_CI = _REPOSITORY_ROOT / ".github/workflows/ci-backend.yml"
_QA_TERRAFORM = _REPOSITORY_ROOT / "terraform/document_transform_qa.tf"


def test_application_container_does_not_run_database_migrations() -> None:
    dockerfile = _DOCKERFILE.read_text()
    assert "alembic upgrade" not in dockerfile
    assert (
        'CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]'
        in dockerfile
    )


def test_qa_container_uses_secretless_static_entrypoint_without_runtime_arguments() -> None:
    dockerfile = _QA_DOCKERFILE.read_text()
    assert "COPY scripts/ scripts/" in dockerfile
    assert all("@sha256:" in line for line in dockerfile.splitlines() if line.startswith("FROM "))
    assert dockerfile.count("FROM ") == 2
    assert "/bin/sh /tmp/prepare-runtime.sh qa" in dockerfile
    runtime_setup = (_QA_DOCKERFILE.parent / "scripts/prepare-runtime.sh").read_text()
    assert "apk add --no-cache poppler-utils=" in runtime_setup
    assert "adduser -D -u 1001 app" in runtime_setup
    assert "USER 1001:1001" in dockerfile
    assert (
        'ENTRYPOINT [".venv/bin/python", "-m", "scripts.qa_document_transform_cloud"]' in dockerfile
    )
    assert "CMD" not in dockerfile


def test_qa_job_is_backend_launched_without_override_permissions_or_transport_secrets() -> None:
    terraform = _QA_TERRAFORM.read_text()
    launcher = terraform[
        terraform.index(
            'resource "google_cloud_run_v2_job_iam_member" "document_transform_qa_launcher"'
        ) : terraform.index(
            'resource "google_cloud_run_v2_job_iam_member" "document_transform_qa_attestor"'
        )
    ]
    assert 'role     = "roles/run.jobsExecutor"' in launcher
    assert "roles/run.jobsExecutorWithOverrides" not in terraform
    assert 'member   = "serviceAccount:${google_service_account.backend.email}"' in launcher
    job = terraform[
        terraform.index(
            'resource "google_cloud_run_v2_job" "document_transform_qa"'
        ) : terraform.index(
            'resource "google_cloud_run_v2_job_iam_member" "document_transform_qa_launcher"'
        )
    ]
    assert "args" not in job
    assert all(
        secret not in job
        for secret in ("CAPABILITY_TOKEN", "GRANT_TOKEN", "RUN_ID", "SECRET_KEY", "FERNET_KEY")
    )


def test_qa_grant_concurrency_suite_is_in_required_postgres_ci() -> None:
    workflow = _BACKEND_CI.read_text()
    postgres_job = workflow[
        workflow.index("  postgres-concurrency:") : workflow.index("\n  image:\n")
    ]
    assert "tests/test_document_transform_qa_grants.py" in postgres_job
    assert "tests/test_document_transform_qa_submit_postgres.py" in postgres_job
    assert "tests/test_document_transform_qa_corpus_postgres.py" in postgres_job
    assert "tests/test_document_transform_qa_campaigns_postgres.py" in postgres_job
    required = workflow[workflow.index("  required:") :]
    assert "needs: [changes, lint-and-typecheck, test, postgres-concurrency, image]" in required
    assert "POSTGRES_RESULT: ${{ needs.postgres-concurrency.result }}" in required
    assert "scripts/github/release_gate.py ci backend" in required


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
