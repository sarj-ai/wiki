from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy import CheckConstraint, Index, Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.document_transform_qa_corpus import (
    DocumentTransformQaCorpusCampaign,
    DocumentTransformQaCorpusMember,
    DocumentTransformQaCorpusSubmission,
)


_MIGRATION = Path(__file__).parents[1] / "alembic" / "versions" / "0022_qa_corpus_campaigns.py"


@runtime_checkable
class _Migration(Protocol):
    op: object
    down_revision: str

    def upgrade(self) -> None: ...

    def downgrade(self) -> None: ...


class _Operations:
    def __init__(self) -> None:
        self.created_tables: list[tuple[str, tuple[object, ...]]] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...], bool]] = []
        self.dropped: list[tuple[str, str]] = []
        self.executed: list[str] = []

    def create_table(self, name: str, *items: object) -> None:
        self.created_tables.append((name, items))

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        *,
        unique: bool = False,
        **_kwargs: object,
    ) -> None:
        self.created_indexes.append((name, table_name, tuple(columns), unique))

    def drop_index(self, name: str, *, table_name: str) -> None:
        self.dropped.append(("index", f"{table_name}.{name}"))

    def drop_table(self, name: str) -> None:
        self.dropped.append(("table", name))

    def execute(self, statement: str) -> None:
        self.executed.append(statement)


def _load_migration() -> _Migration:
    spec = importlib.util.spec_from_file_location("qa_corpus_campaign_migration", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module, _Migration)
    return module


def _constraint_names(table: Table, kind: type[object]) -> set[str]:
    names: set[str] = set()
    for constraint in table.constraints:
        if isinstance(constraint, kind) and isinstance(constraint.name, str):
            names.add(constraint.name)
    return names


def test_0022_upgrade_and_downgrade_are_ordered_and_inactive() -> None:
    migration = _load_migration()
    operations = _Operations()
    migration.op = operations

    migration.upgrade()

    assert migration.down_revision == "0021_qa_run_submissions"
    assert [name for name, _ in operations.created_tables] == [
        "document_transform_qa_corpus_campaigns",
        "document_transform_qa_corpus_members",
        "document_transform_qa_corpus_submissions",
    ]
    assert {index[0] for index in operations.created_indexes} == {
        "ix_qa_corpus_campaigns_created_at",
        "ix_qa_corpus_campaigns_binding",
        "uq_qa_corpus_campaign_active_resume",
        "uq_qa_corpus_submission_task",
        "uq_qa_corpus_submission_output",
    }
    trigger_sql = "\n".join(operations.executed)
    assert "CREATE FUNCTION guard_qa_corpus_campaign_update" in trigger_sql
    assert "CREATE FUNCTION guard_qa_corpus_member_mutation" in trigger_sql
    assert "CREATE FUNCTION guard_qa_corpus_submission_mutation" in trigger_sql
    assert "CREATE FUNCTION validate_qa_corpus_campaign" in trigger_sql
    assert trigger_sql.count("DEFERRABLE INITIALLY DEFERRED") == 3

    migration.downgrade()

    downgrade_sql = "\n".join(operations.executed[len(operations.executed) - 11 :])
    assert "DROP TRIGGER ck_qa_corpus_submission_recheck_campaign" in downgrade_sql
    assert "DROP FUNCTION validate_qa_corpus_campaign(uuid)" in downgrade_sql
    assert operations.dropped == [
        ("index", "document_transform_qa_corpus_campaigns.uq_qa_corpus_campaign_active_resume"),
        ("index", "document_transform_qa_corpus_submissions.uq_qa_corpus_submission_output"),
        ("index", "document_transform_qa_corpus_submissions.uq_qa_corpus_submission_task"),
        ("table", "document_transform_qa_corpus_submissions"),
        ("table", "document_transform_qa_corpus_members"),
        ("index", "document_transform_qa_corpus_campaigns.ix_qa_corpus_campaigns_binding"),
        ("index", "document_transform_qa_corpus_campaigns.ix_qa_corpus_campaigns_created_at"),
        ("table", "document_transform_qa_corpus_campaigns"),
    ]


def test_campaign_model_freezes_exact_authority_and_terminal_attestation() -> None:
    table = DocumentTransformQaCorpusCampaign.__table__
    assert isinstance(table, Table)

    assert set(table.columns.keys()) == {
        "id",
        "qa_run_id",
        "qa_grant_id",
        "user_id",
        "user_email",
        "execution_name",
        "execution_uid",
        "execution_job_name",
        "bootstrap_capability_digest",
        "release_sha",
        "image_digest",
        "mode",
        "source_folder_id",
        "source_drive_id",
        "destination_folder_id",
        "destination_drive_id",
        "corpus_plan_id",
        "campaign_identity_sha256",
        "corpus_plan_sha256",
        "source_root_sha256",
        "destination_identity_sha256",
        "destination_permission_sha256",
        "destination_version_at_plan",
        "document_count",
        "unsupported_count",
        "state",
        "canary_task_plan_id",
        "canary_task_id",
        "canary_result_sha256",
        "canary_evidence_sha256",
        "canary_output_permission_sha256",
        "canary_needs_review",
        "canary_review_finding_count",
        "created_at",
        "canary_verified_at",
        "completed_at",
        "failed_at",
    }
    assert not table.foreign_keys
    assert {
        "ck_qa_corpus_release_sha",
        "ck_qa_corpus_image_digest",
        "ck_qa_corpus_mode",
        "ck_qa_corpus_destination_my_drive",
        "ck_qa_corpus_distinct_folders",
        "ck_qa_corpus_document_count",
        "ck_qa_corpus_unsupported_count",
        "ck_qa_corpus_capability_digest",
        "ck_qa_corpus_campaign_text_bounds",
        "ck_qa_corpus_campaign_digests",
        "ck_qa_corpus_canary_digests",
        "ck_qa_corpus_campaign_state",
        "ck_qa_corpus_canary_attestation",
        "ck_qa_corpus_campaign_state_tuple",
        "ck_qa_corpus_terminal_state",
        "ck_qa_corpus_campaign_timestamps",
    } <= _constraint_names(table, CheckConstraint)
    assert _constraint_names(table, UniqueConstraint) >= {
        "uq_qa_corpus_campaign_run",
        "uq_qa_corpus_campaign_grant",
        "uq_qa_corpus_campaign_execution_name",
        "uq_qa_corpus_campaign_execution_uid",
        "uq_qa_corpus_campaign_capability",
        "uq_qa_corpus_campaign_plan",
        "uq_qa_corpus_campaign_identity",
    }
    assert {index.name for index in table.indexes} >= {
        "ix_qa_corpus_campaigns_created_at",
        "ix_qa_corpus_campaigns_binding",
        "uq_qa_corpus_campaign_active_resume",
    }


def test_members_are_exactly_bounded_and_server_operation_is_unique() -> None:
    table = DocumentTransformQaCorpusMember.__table__
    assert isinstance(table, Table)

    assert _constraint_names(table, CheckConstraint) >= {
        "ck_qa_corpus_member_ordinal",
        "ck_qa_corpus_member_mime",
        "ck_qa_corpus_member_mode",
        "ck_qa_corpus_member_docx_mode",
        "ck_qa_corpus_member_text_bounds",
        "ck_qa_corpus_member_fingerprint",
        "ck_qa_corpus_member_operation_identity",
    }
    assert _constraint_names(table, UniqueConstraint) >= {
        "uq_qa_corpus_member_ordinal",
        "uq_qa_corpus_member_source",
        "uq_qa_corpus_member_campaign_identity",
        "uq_qa_corpus_member_task_plan",
    }
    assert {foreign_key.target_fullname for foreign_key in table.foreign_keys} == {
        "document_transform_qa_corpus_campaigns.id"
    }


def test_submission_is_reservation_first_and_has_no_task_foreign_key() -> None:
    table = DocumentTransformQaCorpusSubmission.__table__
    assert isinstance(table, Table)

    assert "tasks.id" not in {foreign_key.target_fullname for foreign_key in table.foreign_keys}
    assert {foreign_key.target_fullname for foreign_key in table.foreign_keys} == {
        "document_transform_qa_corpus_members.id",
        "document_transform_qa_corpus_members.campaign_id",
    }
    assert _constraint_names(table, CheckConstraint) >= {
        "ck_qa_corpus_submission_state",
        "ck_qa_corpus_submission_operation_identity",
        "ck_qa_corpus_submission_binding_digests",
        "ck_qa_corpus_submission_terminal_digests",
        "ck_qa_corpus_submission_terminal_tuple",
        "ck_qa_corpus_submission_review_count",
        "ck_qa_corpus_submission_text_bounds",
        "ck_qa_corpus_submission_atomic_state",
        "ck_qa_corpus_submission_timestamps",
    }
    assert {index.name for index in table.indexes} == {
        "uq_qa_corpus_submission_task",
        "uq_qa_corpus_submission_output",
    }
    task_index = next(
        index for index in table.indexes if index.name == "uq_qa_corpus_submission_task"
    )
    assert isinstance(task_index, Index)
    assert task_index.unique
    output_index = next(
        index for index in table.indexes if index.name == "uq_qa_corpus_submission_output"
    )
    assert output_index.unique


def test_postgresql_ddl_contains_no_task_fk_and_all_three_tables_compile() -> None:
    campaign_table = DocumentTransformQaCorpusCampaign.__table__
    member_table = DocumentTransformQaCorpusMember.__table__
    submission_table = DocumentTransformQaCorpusSubmission.__table__
    assert isinstance(campaign_table, Table)
    assert isinstance(member_table, Table)
    assert isinstance(submission_table, Table)
    tables = (campaign_table, member_table, submission_table)
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in tables
    )

    assert "FOREIGN KEY(task_id)" not in ddl
    assert "ordinal BETWEEN 0 AND 19" in ddl
    assert "document_count = 20" in ddl
    assert "unsupported_count = 0" in ddl
    assert "task_plan_id = idempotency_key" in ddl
    assert "canary_output_permission_sha256" in ddl
    assert "canary_needs_review IS FALSE" in ddl
    assert "completed_at >= canary_verified_at" in ddl
