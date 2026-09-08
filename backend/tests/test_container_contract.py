from pathlib import Path


_DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
_QA_DOCKERFILE = Path(__file__).parents[1] / "Dockerfile.qa"
_REPOSITORY_ROOT = Path(__file__).parents[2]
_DEPLOY_WORKFLOW = _REPOSITORY_ROOT / ".github/workflows/deploy-backend.yml"
_DEPLOY_CANDIDATE = _REPOSITORY_ROOT / "scripts/github/deploy-backend-candidate.sh"
_RUN_MIGRATIONS = _REPOSITORY_ROOT / "scripts/github/run-backend-migrations.sh"
_VERIFY_LOGS = _REPOSITORY_ROOT / "scripts/github/verify-backend-correlated-logs.sh"
_MIGRATIONS_TERRAFORM = _REPOSITORY_ROOT / "terraform/dammam.tf"
_BACKEND_CI = _REPOSITORY_ROOT / ".github/workflows/ci-backend.yml"


def test_application_container_does_not_run_database_migrations() -> None:
    dockerfile = _DOCKERFILE.read_text()
    assert "alembic upgrade" not in dockerfile
    assert (
        'CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]'
        in dockerfile
    )


def test_qa_container_reuses_corpus_runner_and_fails_closed_without_arguments() -> None:
    dockerfile = _QA_DOCKERFILE.read_text()
    assert "COPY scripts/ scripts/" in dockerfile
    assert all("@sha256:" in line for line in dockerfile.splitlines() if line.startswith("FROM "))
    assert dockerfile.count("FROM ") == 2
    assert "apk add --no-cache poppler-utils" in dockerfile
    assert "USER app" in dockerfile
    assert (
        'ENTRYPOINT [".venv/bin/python", "-m", "scripts.qa_document_transform_cloud"]' in dockerfile
    )
    assert "CMD" not in dockerfile


def test_qa_grant_concurrency_suite_is_in_required_postgres_ci() -> None:
    workflow = _BACKEND_CI.read_text()
    postgres_job = workflow[
        workflow.index("  postgres-concurrency:") : workflow.index("\n  image:\n")
    ]
    assert "tests/test_document_transform_qa_grants.py" in postgres_job
    assert "tests/test_document_transform_qa_submit_postgres.py" in postgres_job
    required = workflow[workflow.index("  required:") :]
    assert "needs: [lint-and-typecheck, test, postgres-concurrency, image]" in required
    assert 'test "$POSTGRES_RESULT" = success' in required


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
