from pathlib import Path


_DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"
_DEPLOY_WORKFLOW = Path(__file__).parents[2] / ".github/workflows/deploy-backend.yml"
_MIGRATIONS_TERRAFORM = Path(__file__).parents[2] / "terraform/migrations.tf"


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
    assert "--no-traffic" in workflow[candidate:migration]
    assert 'select(.type == "Ready")' in workflow[candidate:migration]
    assert "latestReadyRevisionName" not in workflow[candidate:migration]
    assert "--wait" in workflow[migration:traffic]
    assert 'steps.deploy.outputs.release_image }}"' in workflow[migration:traffic]


def test_migration_job_has_database_only_environment() -> None:
    migration_job = _MIGRATIONS_TERRAFORM.read_text()
    assert "SECRET_KEY" not in migration_job
    assert "FERNET_KEY" not in migration_job
    assert "GOOGLE_CLIENT" not in migration_job
