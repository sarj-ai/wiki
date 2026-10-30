import sqlalchemy as sa

from alembic import op


revision = "0022_qa_corpus_campaigns"
down_revision = "0021_qa_run_submissions"
branch_labels = None
depends_on = None

_SUBMISSIONS_TABLE = "document_transform_qa_corpus_submissions"


def upgrade() -> None:
    op.create_table(
        "document_transform_qa_corpus_campaigns",
        sa.Column("id", sa.UUID(), nullable=False),
        # Immutable snapshots: never foreign keys to expiring QA authority rows.
        sa.Column("qa_run_id", sa.UUID(), nullable=False),
        sa.Column("qa_grant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("user_email", sa.Text(), nullable=False),
        sa.Column("execution_name", sa.Text(), nullable=False),
        sa.Column("execution_uid", sa.UUID(), nullable=False),
        sa.Column("execution_job_name", sa.Text(), nullable=False),
        sa.Column("bootstrap_capability_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("release_sha", sa.Text(), nullable=False),
        sa.Column("image_digest", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("source_folder_id", sa.Text(), nullable=False),
        sa.Column("source_drive_id", sa.Text()),
        sa.Column("destination_folder_id", sa.Text(), nullable=False),
        sa.Column("destination_drive_id", sa.Text()),
        sa.Column("corpus_plan_id", sa.UUID(), nullable=False),
        sa.Column("campaign_identity_sha256", sa.Text(), nullable=False),
        sa.Column("corpus_plan_sha256", sa.Text(), nullable=False),
        sa.Column("source_root_sha256", sa.Text(), nullable=False),
        sa.Column("destination_identity_sha256", sa.Text(), nullable=False),
        sa.Column("destination_permission_sha256", sa.Text(), nullable=False),
        sa.Column("destination_version_at_plan", sa.Text(), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("unsupported_count", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("canary_task_plan_id", sa.UUID()),
        # Snapshot identities only; task retention remains independent.
        sa.Column("canary_task_id", sa.UUID()),
        sa.Column("canary_result_sha256", sa.Text()),
        sa.Column("canary_evidence_sha256", sa.Text()),
        sa.Column("canary_output_permission_sha256", sa.Text()),
        sa.Column("canary_needs_review", sa.Boolean()),
        sa.Column("canary_review_finding_count", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canary_verified_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("release_sha ~ '^[0-9a-f]{40}$'", name="ck_qa_corpus_release_sha"),
        sa.CheckConstraint(
            "image_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_qa_corpus_image_digest"
        ),
        sa.CheckConstraint("mode IN ('format', 'unslop', 'both')", name="ck_qa_corpus_mode"),
        sa.CheckConstraint(
            "destination_drive_id IS NULL", name="ck_qa_corpus_destination_my_drive"
        ),
        sa.CheckConstraint(
            "source_folder_id <> destination_folder_id", name="ck_qa_corpus_distinct_folders"
        ),
        sa.CheckConstraint("document_count = 20", name="ck_qa_corpus_document_count"),
        sa.CheckConstraint("unsupported_count = 0", name="ck_qa_corpus_unsupported_count"),
        sa.CheckConstraint(
            "octet_length(bootstrap_capability_digest) = 32",
            name="ck_qa_corpus_capability_digest",
        ),
        sa.CheckConstraint(
            "char_length(user_email) BETWEEN 3 AND 320 "
            "AND char_length(execution_name) BETWEEN 1 AND 1024 "
            "AND char_length(execution_job_name) BETWEEN 1 AND 1024 "
            "AND char_length(source_folder_id) BETWEEN 1 AND 255 "
            "AND (source_drive_id IS NULL OR char_length(source_drive_id) BETWEEN 1 AND 255) "
            "AND char_length(destination_folder_id) BETWEEN 1 AND 255 "
            "AND char_length(destination_version_at_plan) BETWEEN 1 AND 255",
            name="ck_qa_corpus_campaign_text_bounds",
        ),
        sa.CheckConstraint(
            "campaign_identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND corpus_plan_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_root_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_permission_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_qa_corpus_campaign_digests",
        ),
        sa.CheckConstraint(
            "(canary_result_sha256 IS NULL OR canary_result_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (canary_evidence_sha256 IS NULL OR canary_evidence_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (canary_output_permission_sha256 IS NULL "
            "OR canary_output_permission_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_qa_corpus_canary_digests",
        ),
        sa.CheckConstraint(
            "state IN ('planned', 'canary_reserved', 'canary_submitted', "
            "'canary_verified', 'running', 'verified', 'failed')",
            name="ck_qa_corpus_campaign_state",
        ),
        sa.CheckConstraint(
            "(canary_task_plan_id IS NULL AND canary_task_id IS NULL "
            "AND canary_result_sha256 IS NULL AND canary_evidence_sha256 IS NULL "
            "AND canary_output_permission_sha256 IS NULL AND canary_needs_review IS NULL "
            "AND canary_review_finding_count IS NULL AND canary_verified_at IS NULL) OR "
            "(canary_task_plan_id IS NOT NULL "
            "AND canary_task_id IS NULL AND canary_result_sha256 IS NULL "
            "AND canary_evidence_sha256 IS NULL AND canary_output_permission_sha256 IS NULL "
            "AND canary_needs_review IS NULL AND canary_review_finding_count IS NULL "
            "AND canary_verified_at IS NULL) OR "
            "(canary_task_plan_id IS NOT NULL "
            "AND canary_task_id IS NOT NULL AND canary_result_sha256 IS NULL "
            "AND canary_evidence_sha256 IS NULL AND canary_output_permission_sha256 IS NULL "
            "AND canary_needs_review IS NULL AND canary_review_finding_count IS NULL "
            "AND canary_verified_at IS NULL) OR "
            "(canary_task_plan_id IS NOT NULL AND canary_task_id IS NOT NULL "
            "AND canary_result_sha256 IS NOT NULL AND canary_evidence_sha256 IS NOT NULL "
            "AND canary_output_permission_sha256 IS NOT NULL "
            "AND canary_needs_review IS FALSE AND canary_review_finding_count = 0 "
            "AND canary_verified_at IS NOT NULL)",
            name="ck_qa_corpus_canary_attestation",
        ),
        sa.CheckConstraint(
            "(state = 'planned' AND canary_task_plan_id IS NULL) OR "
            "(state = 'canary_reserved' AND canary_task_plan_id IS NOT NULL "
            "AND canary_task_id IS NULL) OR "
            "(state = 'canary_submitted' AND canary_task_id IS NOT NULL "
            "AND canary_result_sha256 IS NULL) OR "
            "(state IN ('canary_verified', 'running', 'verified') "
            "AND canary_result_sha256 IS NOT NULL) OR state = 'failed'",
            name="ck_qa_corpus_campaign_state_tuple",
        ),
        sa.CheckConstraint(
            "(state = 'verified' AND completed_at IS NOT NULL AND failed_at IS NULL) OR "
            "(state = 'failed' AND failed_at IS NOT NULL AND completed_at IS NULL) OR "
            "(state NOT IN ('verified', 'failed') AND completed_at IS NULL AND failed_at IS NULL)",
            name="ck_qa_corpus_terminal_state",
        ),
        sa.CheckConstraint(
            "(canary_verified_at IS NULL OR canary_verified_at >= created_at) "
            "AND (completed_at IS NULL OR (completed_at >= created_at "
            "AND (canary_verified_at IS NULL OR completed_at >= canary_verified_at))) "
            "AND (failed_at IS NULL OR (failed_at >= created_at "
            "AND (canary_verified_at IS NULL OR failed_at >= canary_verified_at)))",
            name="ck_qa_corpus_campaign_timestamps",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("qa_run_id", name="uq_qa_corpus_campaign_run"),
        sa.UniqueConstraint("qa_grant_id", name="uq_qa_corpus_campaign_grant"),
        sa.UniqueConstraint("execution_name", name="uq_qa_corpus_campaign_execution_name"),
        sa.UniqueConstraint("execution_uid", name="uq_qa_corpus_campaign_execution_uid"),
        sa.UniqueConstraint("bootstrap_capability_digest", name="uq_qa_corpus_campaign_capability"),
        sa.UniqueConstraint("corpus_plan_id", name="uq_qa_corpus_campaign_plan"),
        sa.UniqueConstraint("campaign_identity_sha256", name="uq_qa_corpus_campaign_identity"),
    )
    op.create_index(
        "ix_qa_corpus_campaigns_created_at",
        "document_transform_qa_corpus_campaigns",
        ["created_at"],
    )
    op.create_index(
        "uq_qa_corpus_campaign_active_resume",
        "document_transform_qa_corpus_campaigns",
        ["user_id", "source_folder_id", "destination_folder_id", "mode"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('verified', 'failed')"),
    )
    op.create_index(
        "ix_qa_corpus_campaigns_binding",
        "document_transform_qa_corpus_campaigns",
        ["user_id", "source_folder_id", "state"],
    )

    op.create_table(
        "document_transform_qa_corpus_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_file_id", sa.Text(), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("source_parent_folder_id", sa.Text(), nullable=False),
        sa.Column("source_drive_id", sa.Text()),
        sa.Column("source_version", sa.Text(), nullable=False),
        sa.Column("source_modified_time", sa.Text(), nullable=False),
        sa.Column("source_fingerprint_sha256", sa.Text(), nullable=False),
        sa.Column("selected_mode", sa.Text(), nullable=False),
        sa.Column("task_plan_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ordinal BETWEEN 0 AND 19", name="ck_qa_corpus_member_ordinal"),
        sa.CheckConstraint(
            "mime_type IN ('application/vnd.google-apps.document', "
            "'application/vnd.openxmlformats-officedocument.wordprocessingml.document')",
            name="ck_qa_corpus_member_mime",
        ),
        sa.CheckConstraint(
            "selected_mode IN ('format', 'unslop', 'both')", name="ck_qa_corpus_member_mode"
        ),
        sa.CheckConstraint(
            "mime_type <> 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
            "OR selected_mode = 'format'",
            name="ck_qa_corpus_member_docx_mode",
        ),
        sa.CheckConstraint(
            "char_length(source_file_id) BETWEEN 1 AND 255 "
            "AND char_length(source_name) BETWEEN 1 AND 1024 "
            "AND char_length(source_parent_folder_id) BETWEEN 1 AND 255 "
            "AND (source_drive_id IS NULL OR char_length(source_drive_id) BETWEEN 1 AND 255) "
            "AND char_length(source_version) BETWEEN 1 AND 255 "
            "AND char_length(source_modified_time) BETWEEN 1 AND 128",
            name="ck_qa_corpus_member_text_bounds",
        ),
        sa.CheckConstraint(
            "source_fingerprint_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_qa_corpus_member_fingerprint",
        ),
        sa.CheckConstraint(
            "task_plan_id = idempotency_key", name="ck_qa_corpus_member_operation_identity"
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["document_transform_qa_corpus_campaigns.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "ordinal", name="uq_qa_corpus_member_ordinal"),
        sa.UniqueConstraint("campaign_id", "source_file_id", name="uq_qa_corpus_member_source"),
        sa.UniqueConstraint("id", "campaign_id", name="uq_qa_corpus_member_campaign_identity"),
        sa.UniqueConstraint("task_plan_id", name="uq_qa_corpus_member_task_plan"),
    )

    op.create_table(
        _SUBMISSIONS_TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("task_plan_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.UUID(), nullable=False),
        sa.Column("source_fingerprint_sha256", sa.Text(), nullable=False),
        sa.Column("destination_identity_sha256", sa.Text(), nullable=False),
        sa.Column("destination_permission_sha256", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        # Deliberately no task foreign key.
        sa.Column("task_id", sa.UUID()),
        sa.Column("output_file_id", sa.Text()),
        sa.Column("output_revision_id", sa.Text()),
        sa.Column("result_sha256", sa.Text()),
        sa.Column("evidence_sha256", sa.Text()),
        sa.Column("output_permission_sha256", sa.Text()),
        sa.Column("needs_review", sa.Boolean()),
        sa.Column("review_finding_count", sa.Integer()),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('reserved', 'submitted', 'verified', 'failed')",
            name="ck_qa_corpus_submission_state",
        ),
        sa.CheckConstraint(
            "task_plan_id = idempotency_key", name="ck_qa_corpus_submission_operation_identity"
        ),
        sa.CheckConstraint(
            "source_fingerprint_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_permission_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_qa_corpus_submission_binding_digests",
        ),
        sa.CheckConstraint(
            "(result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (output_permission_sha256 IS NULL "
            "OR output_permission_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_qa_corpus_submission_terminal_digests",
        ),
        sa.CheckConstraint(
            "(output_file_id IS NULL AND output_revision_id IS NULL "
            "AND result_sha256 IS NULL AND evidence_sha256 IS NULL "
            "AND output_permission_sha256 IS NULL AND needs_review IS NULL "
            "AND review_finding_count IS NULL) OR "
            "(output_file_id IS NOT NULL AND output_revision_id IS NOT NULL "
            "AND result_sha256 IS NOT NULL AND evidence_sha256 IS NOT NULL "
            "AND output_permission_sha256 IS NOT NULL AND needs_review IS NOT NULL "
            "AND review_finding_count IS NOT NULL)",
            name="ck_qa_corpus_submission_terminal_tuple",
        ),
        sa.CheckConstraint(
            "review_finding_count IS NULL OR review_finding_count >= 0",
            name="ck_qa_corpus_submission_review_count",
        ),
        sa.CheckConstraint(
            "(output_file_id IS NULL OR char_length(output_file_id) BETWEEN 1 AND 255) "
            "AND (output_revision_id IS NULL OR char_length(output_revision_id) BETWEEN 1 AND 255)",
            name="ck_qa_corpus_submission_text_bounds",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND task_id IS NULL AND submitted_at IS NULL "
            "AND output_file_id IS NULL AND verified_at IS NULL AND failed_at IS NULL) OR "
            "(state = 'submitted' AND task_id IS NOT NULL AND submitted_at IS NOT NULL "
            "AND output_file_id IS NULL AND verified_at IS NULL AND failed_at IS NULL) OR "
            "(state = 'verified' AND task_id IS NOT NULL AND submitted_at IS NOT NULL "
            "AND output_file_id IS NOT NULL AND output_revision_id IS NOT NULL "
            "AND result_sha256 IS NOT NULL AND evidence_sha256 IS NOT NULL "
            "AND output_permission_sha256 IS NOT NULL AND verified_at IS NOT NULL "
            "AND failed_at IS NULL) OR "
            "(state = 'failed' AND ((task_id IS NULL AND submitted_at IS NULL) OR "
            "(task_id IS NOT NULL AND submitted_at IS NOT NULL)) "
            "AND output_file_id IS NULL AND verified_at IS NULL AND failed_at IS NOT NULL)",
            name="ck_qa_corpus_submission_atomic_state",
        ),
        sa.CheckConstraint(
            "(submitted_at IS NULL OR submitted_at >= reserved_at) "
            "AND (verified_at IS NULL OR (verified_at >= reserved_at "
            "AND verified_at >= submitted_at)) "
            "AND (failed_at IS NULL OR (failed_at >= reserved_at "
            "AND (submitted_at IS NULL OR failed_at >= submitted_at)))",
            name="ck_qa_corpus_submission_timestamps",
        ),
        sa.ForeignKeyConstraint(
            ["member_id", "campaign_id"],
            [
                "document_transform_qa_corpus_members.id",
                "document_transform_qa_corpus_members.campaign_id",
            ],
            name="fk_qa_corpus_submission_member_campaign",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id", name="uq_qa_corpus_submission_member"),
        sa.UniqueConstraint("task_plan_id", name="uq_qa_corpus_submission_task_plan"),
    )
    op.create_index(
        "uq_qa_corpus_submission_task",
        _SUBMISSIONS_TABLE,
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )
    op.create_index(
        "uq_qa_corpus_submission_output",
        _SUBMISSIONS_TABLE,
        ["campaign_id", "output_file_id"],
        unique=True,
        postgresql_where=sa.text("output_file_id IS NOT NULL"),
    )
    op.execute(  # sarj-noqa: SARJ470 — DB guards preserve immutable QA campaign authority.
        """
        CREATE FUNCTION guard_qa_corpus_campaign_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'QA corpus campaign audit rows cannot be deleted';
            END IF;
            IF OLD.state IN ('verified', 'failed') AND OLD IS DISTINCT FROM NEW THEN
                RAISE EXCEPTION 'QA corpus terminal campaigns are immutable';
            END IF;
            IF ROW(OLD.id, OLD.qa_run_id, OLD.qa_grant_id, OLD.user_id, OLD.user_email,
                   OLD.execution_name, OLD.execution_uid, OLD.execution_job_name,
                   OLD.bootstrap_capability_digest, OLD.release_sha, OLD.image_digest, OLD.mode,
                   OLD.source_folder_id, OLD.source_drive_id, OLD.destination_folder_id,
                   OLD.destination_drive_id, OLD.corpus_plan_id, OLD.campaign_identity_sha256,
                   OLD.corpus_plan_sha256, OLD.source_root_sha256,
                   OLD.destination_identity_sha256, OLD.destination_permission_sha256,
                   OLD.destination_version_at_plan, OLD.document_count, OLD.unsupported_count,
                   OLD.created_at)
               IS DISTINCT FROM
               ROW(NEW.id, NEW.qa_run_id, NEW.qa_grant_id, NEW.user_id, NEW.user_email,
                   NEW.execution_name, NEW.execution_uid, NEW.execution_job_name,
                   NEW.bootstrap_capability_digest, NEW.release_sha, NEW.image_digest, NEW.mode,
                   NEW.source_folder_id, NEW.source_drive_id, NEW.destination_folder_id,
                   NEW.destination_drive_id, NEW.corpus_plan_id, NEW.campaign_identity_sha256,
                   NEW.corpus_plan_sha256, NEW.source_root_sha256,
                   NEW.destination_identity_sha256, NEW.destination_permission_sha256,
                   NEW.destination_version_at_plan, NEW.document_count, NEW.unsupported_count,
                   NEW.created_at) THEN
                RAISE EXCEPTION 'QA corpus campaign authority is immutable';
            END IF;
            IF (OLD.canary_task_plan_id IS NOT NULL AND
                OLD.canary_task_plan_id IS DISTINCT FROM NEW.canary_task_plan_id) OR
               (OLD.canary_task_id IS NOT NULL AND
                OLD.canary_task_id IS DISTINCT FROM NEW.canary_task_id) OR
               (OLD.canary_result_sha256 IS NOT NULL AND
                OLD.canary_result_sha256 IS DISTINCT FROM NEW.canary_result_sha256) OR
               (OLD.canary_evidence_sha256 IS NOT NULL AND
                OLD.canary_evidence_sha256 IS DISTINCT FROM NEW.canary_evidence_sha256) OR
               (OLD.canary_output_permission_sha256 IS NOT NULL AND
                OLD.canary_output_permission_sha256 IS DISTINCT FROM
                    NEW.canary_output_permission_sha256) OR
               (OLD.canary_needs_review IS NOT NULL AND
                OLD.canary_needs_review IS DISTINCT FROM NEW.canary_needs_review) OR
               (OLD.canary_review_finding_count IS NOT NULL AND
                OLD.canary_review_finding_count IS DISTINCT FROM
                    NEW.canary_review_finding_count) OR
               (OLD.canary_verified_at IS NOT NULL AND
                OLD.canary_verified_at IS DISTINCT FROM NEW.canary_verified_at) THEN
                RAISE EXCEPTION 'QA corpus canary attestation is immutable once bound';
            END IF;
            IF NOT (
                OLD.state = NEW.state OR
                (OLD.state = 'planned' AND NEW.state IN ('canary_reserved', 'failed')) OR
                (OLD.state = 'canary_reserved' AND NEW.state IN ('canary_submitted', 'failed')) OR
                (OLD.state = 'canary_submitted' AND NEW.state IN ('canary_verified', 'failed')) OR
                (OLD.state = 'canary_verified' AND NEW.state IN ('running', 'failed')) OR
                (OLD.state = 'running' AND NEW.state IN ('verified', 'failed'))
            ) THEN
                RAISE EXCEPTION 'QA corpus campaign state transition is not monotonic';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(  # sarj-noqa: SARJ470 — DB trigger enforces immutable QA campaign authority.
        """
        CREATE TRIGGER guard_qa_corpus_campaign_update
        BEFORE UPDATE OR DELETE ON document_transform_qa_corpus_campaigns
        FOR EACH ROW EXECUTE FUNCTION guard_qa_corpus_campaign_update();
        """
    )
    op.execute(  # sarj-noqa: SARJ470 — DB guards freeze the attested QA corpus membership.
        """
        CREATE FUNCTION guard_qa_corpus_member_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            campaign_state text;
            campaign_mode text;
            campaign_source_drive text;
            bound_campaign uuid;
        BEGIN
            bound_campaign := CASE WHEN TG_OP = 'DELETE' THEN OLD.campaign_id ELSE NEW.campaign_id END;
            SELECT state, mode, source_drive_id
              INTO campaign_state, campaign_mode, campaign_source_drive
              FROM document_transform_qa_corpus_campaigns
             WHERE id = bound_campaign
             FOR UPDATE;
            IF NOT FOUND OR campaign_state <> 'planned' THEN
                RAISE EXCEPTION 'QA corpus members are frozen after planning';
            END IF;
            IF TG_OP <> 'DELETE' AND
               (NEW.selected_mode IS DISTINCT FROM campaign_mode OR
                NEW.source_drive_id IS DISTINCT FROM campaign_source_drive) THEN
                RAISE EXCEPTION 'QA corpus member authority differs from campaign';
            END IF;
            IF TG_OP = 'UPDATE' AND OLD IS DISTINCT FROM NEW THEN
                RAISE EXCEPTION 'QA corpus members are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(  # sarj-noqa: SARJ470 — DB trigger freezes the attested QA corpus membership.
        """
        CREATE TRIGGER guard_qa_corpus_member_mutation
        BEFORE INSERT OR UPDATE OR DELETE ON document_transform_qa_corpus_members
        FOR EACH ROW EXECUTE FUNCTION guard_qa_corpus_member_mutation();
        """
    )
    op.execute(  # sarj-noqa: SARJ470 — DB guards freeze QA submission authority and terminal results.
        """
        CREATE FUNCTION guard_qa_corpus_submission_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            campaign_state text;
            expected_plan uuid;
            expected_source_hash text;
            expected_destination_hash text;
            expected_permission_hash text;
        BEGIN
            SELECT c.state, m.task_plan_id, m.source_fingerprint_sha256,
                   c.destination_identity_sha256, c.destination_permission_sha256
              INTO campaign_state, expected_plan, expected_source_hash,
                   expected_destination_hash, expected_permission_hash
              FROM document_transform_qa_corpus_campaigns c
              JOIN document_transform_qa_corpus_members m
                ON m.campaign_id = c.id
             WHERE c.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.campaign_id ELSE NEW.campaign_id END
               AND m.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.member_id ELSE NEW.member_id END
             FOR UPDATE OF c;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'QA corpus submission has no exact member campaign';
            END IF;
            IF campaign_state IN ('verified', 'failed') THEN
                RAISE EXCEPTION 'QA corpus terminal submissions are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'QA corpus submission audit rows cannot be deleted';
            END IF;
            IF TG_OP = 'INSERT' AND campaign_state NOT IN
               ('canary_reserved', 'canary_submitted', 'canary_verified', 'running') THEN
                RAISE EXCEPTION 'QA corpus submissions require a frozen campaign';
            END IF;
            IF TG_OP <> 'DELETE' AND
               (NEW.task_plan_id IS DISTINCT FROM expected_plan OR
                NEW.idempotency_key IS DISTINCT FROM expected_plan OR
                NEW.source_fingerprint_sha256 IS DISTINCT FROM expected_source_hash OR
                NEW.destination_identity_sha256 IS DISTINCT FROM expected_destination_hash OR
                NEW.destination_permission_sha256 IS DISTINCT FROM expected_permission_hash) THEN
                RAISE EXCEPTION 'QA corpus submission authority differs from frozen plan';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF OLD.state IN ('verified', 'failed') AND OLD IS DISTINCT FROM NEW THEN
                    RAISE EXCEPTION 'QA corpus terminal submissions are immutable';
                END IF;
                IF ROW(OLD.id, OLD.campaign_id, OLD.member_id, OLD.task_plan_id,
                       OLD.idempotency_key, OLD.source_fingerprint_sha256,
                       OLD.destination_identity_sha256, OLD.destination_permission_sha256,
                       OLD.reserved_at)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.campaign_id, NEW.member_id, NEW.task_plan_id,
                       NEW.idempotency_key, NEW.source_fingerprint_sha256,
                       NEW.destination_identity_sha256, NEW.destination_permission_sha256,
                       NEW.reserved_at) THEN
                    RAISE EXCEPTION 'QA corpus submission binding is immutable';
                END IF;
                IF (OLD.task_id IS NOT NULL AND OLD.task_id IS DISTINCT FROM NEW.task_id) OR
                   (OLD.output_file_id IS NOT NULL AND
                    OLD.output_file_id IS DISTINCT FROM NEW.output_file_id) OR
                   (OLD.output_revision_id IS NOT NULL AND
                    OLD.output_revision_id IS DISTINCT FROM NEW.output_revision_id) OR
                   (OLD.result_sha256 IS NOT NULL AND
                    OLD.result_sha256 IS DISTINCT FROM NEW.result_sha256) OR
                   (OLD.evidence_sha256 IS NOT NULL AND
                    OLD.evidence_sha256 IS DISTINCT FROM NEW.evidence_sha256) OR
                   (OLD.output_permission_sha256 IS NOT NULL AND
                    OLD.output_permission_sha256 IS DISTINCT FROM NEW.output_permission_sha256) OR
                   (OLD.needs_review IS NOT NULL AND
                    OLD.needs_review IS DISTINCT FROM NEW.needs_review) OR
                   (OLD.review_finding_count IS NOT NULL AND
                    OLD.review_finding_count IS DISTINCT FROM NEW.review_finding_count) OR
                   (OLD.submitted_at IS NOT NULL AND
                    OLD.submitted_at IS DISTINCT FROM NEW.submitted_at) OR
                   (OLD.verified_at IS NOT NULL AND
                    OLD.verified_at IS DISTINCT FROM NEW.verified_at) OR
                   (OLD.failed_at IS NOT NULL AND
                    OLD.failed_at IS DISTINCT FROM NEW.failed_at) THEN
                    RAISE EXCEPTION 'QA corpus submission evidence is immutable once bound';
                END IF;
                IF NOT (
                    OLD.state = NEW.state OR
                    (OLD.state = 'reserved' AND NEW.state IN ('submitted', 'failed')) OR
                    (OLD.state = 'submitted' AND NEW.state IN ('verified', 'failed'))
                ) THEN
                    RAISE EXCEPTION 'QA corpus submission state transition is not monotonic';
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_qa_corpus_submission_mutation
        BEFORE INSERT OR UPDATE OR DELETE ON __QA_CORPUS_SUBMISSIONS__
        FOR EACH ROW EXECUTE FUNCTION guard_qa_corpus_submission_mutation();
        """.replace("__QA_CORPUS_SUBMISSIONS__", _SUBMISSIONS_TABLE)
    )
    op.execute(
        """
        CREATE FUNCTION validate_qa_corpus_campaign(campaign_key uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            campaign_row document_transform_qa_corpus_campaigns%ROWTYPE;
            member_count integer;
            submission_count integer;
            verified_count integer;
            invalid_binding_count integer;
            canary_plan uuid;
            canary_task uuid;
            canary_result text;
            canary_evidence text;
            canary_permission text;
            canary_needs_review boolean;
            canary_finding_count integer;
            canary_state text;
        BEGIN
            SELECT * INTO campaign_row
              FROM document_transform_qa_corpus_campaigns
             WHERE id = campaign_key;
            IF NOT FOUND THEN
                RETURN;
            END IF;
            SELECT count(*) INTO member_count
              FROM document_transform_qa_corpus_members
             WHERE campaign_id = campaign_key;
            IF campaign_row.state NOT IN ('planned', 'failed') AND member_count <> 20 THEN
                RAISE EXCEPTION 'QA corpus campaign requires exactly 20 frozen members';
            END IF;
            IF campaign_row.state NOT IN ('planned', 'failed') AND NOT EXISTS (
                SELECT 1 FROM document_transform_qa_corpus_members
                 WHERE campaign_id = campaign_key AND ordinal = 0
                   AND task_plan_id = campaign_row.canary_task_plan_id
            ) THEN
                RAISE EXCEPTION 'QA corpus campaign canary differs from ordinal zero';
            END IF;
            SELECT count(*) INTO invalid_binding_count
              FROM __QA_CORPUS_SUBMISSIONS__ s
              JOIN document_transform_qa_corpus_members m
                ON m.id = s.member_id AND m.campaign_id = s.campaign_id
             WHERE s.campaign_id = campaign_key
               AND (s.task_plan_id IS DISTINCT FROM m.task_plan_id OR
                    s.idempotency_key IS DISTINCT FROM m.task_plan_id OR
                    s.source_fingerprint_sha256 IS DISTINCT FROM m.source_fingerprint_sha256 OR
                    s.destination_identity_sha256 IS DISTINCT FROM
                        campaign_row.destination_identity_sha256 OR
                    s.destination_permission_sha256 IS DISTINCT FROM
                        campaign_row.destination_permission_sha256);
            IF invalid_binding_count <> 0 THEN
                RAISE EXCEPTION 'QA corpus submission binding differs from campaign plan';
            END IF;
            IF campaign_row.state = 'canary_submitted' THEN
                SELECT s.task_plan_id, s.task_id, s.state
                  INTO canary_plan, canary_task, canary_state
                  FROM __QA_CORPUS_SUBMISSIONS__ s
                  JOIN document_transform_qa_corpus_members m
                    ON m.id = s.member_id AND m.campaign_id = s.campaign_id
                 WHERE s.campaign_id = campaign_key AND m.ordinal = 0;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'QA corpus canary submission is missing';
                END IF;
                IF canary_state NOT IN ('submitted', 'verified') OR
                   canary_plan IS DISTINCT FROM campaign_row.canary_task_plan_id OR
                   canary_task IS DISTINCT FROM campaign_row.canary_task_id THEN
                    RAISE EXCEPTION 'QA corpus canary submission is not exactly bound';
                END IF;
            END IF;
            IF campaign_row.state IN ('canary_verified', 'running', 'verified') THEN
                SELECT s.task_plan_id, s.task_id, s.result_sha256, s.evidence_sha256,
                       s.output_permission_sha256, s.needs_review,
                       s.review_finding_count, s.state
                  INTO canary_plan, canary_task, canary_result, canary_evidence,
                       canary_permission, canary_needs_review, canary_finding_count,
                       canary_state
                  FROM __QA_CORPUS_SUBMISSIONS__ s
                  JOIN document_transform_qa_corpus_members m
                    ON m.id = s.member_id AND m.campaign_id = s.campaign_id
                 WHERE s.campaign_id = campaign_key AND m.ordinal = 0;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'QA corpus canary lacks exact verified attestation';
                END IF;
                IF canary_state <> 'verified' OR
                   canary_plan IS DISTINCT FROM campaign_row.canary_task_plan_id OR
                   canary_task IS DISTINCT FROM campaign_row.canary_task_id OR
                   canary_result IS DISTINCT FROM campaign_row.canary_result_sha256 OR
                   canary_evidence IS DISTINCT FROM campaign_row.canary_evidence_sha256 OR
                   canary_permission IS DISTINCT FROM
                       campaign_row.canary_output_permission_sha256 OR
                   canary_needs_review IS DISTINCT FROM campaign_row.canary_needs_review OR
                   canary_finding_count IS DISTINCT FROM
                       campaign_row.canary_review_finding_count THEN
                    RAISE EXCEPTION 'QA corpus canary lacks exact verified attestation';
                END IF;
            END IF;
            IF campaign_row.state = 'verified' THEN
                SELECT count(*), count(*) FILTER (WHERE state = 'verified')
                  INTO submission_count, verified_count
                  FROM __QA_CORPUS_SUBMISSIONS__
                 WHERE campaign_id = campaign_key;
                IF submission_count <> 20 OR verified_count <> 20 THEN
                    RAISE EXCEPTION 'QA corpus completion requires 20 verified submissions';
                END IF;
            END IF;
        END;
        $$;
        """.replace("__QA_CORPUS_SUBMISSIONS__", _SUBMISSIONS_TABLE)
    )
    op.execute(  # sarj-noqa: SARJ470 — DB rechecks enforce the deferred corpus completeness invariant.
        """
        CREATE FUNCTION recheck_qa_corpus_campaign() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            new_campaign uuid;
            old_campaign uuid;
        BEGIN
            IF TG_TABLE_NAME = 'document_transform_qa_corpus_campaigns' THEN
                new_campaign := NEW.id;
                IF TG_OP = 'UPDATE' THEN
                    old_campaign := OLD.id;
                END IF;
            ELSIF TG_OP = 'DELETE' THEN
                old_campaign := OLD.campaign_id;
            ELSE
                new_campaign := NEW.campaign_id;
                IF TG_OP = 'UPDATE' THEN
                    old_campaign := OLD.campaign_id;
                END IF;
            END IF;
            IF new_campaign IS NOT NULL THEN
                PERFORM validate_qa_corpus_campaign(new_campaign);
            END IF;
            IF old_campaign IS NOT NULL AND old_campaign IS DISTINCT FROM new_campaign THEN
                PERFORM validate_qa_corpus_campaign(old_campaign);
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(  # sarj-noqa: SARJ470 — DB constraint trigger validates a campaign at transaction commit.
        """
        CREATE CONSTRAINT TRIGGER ck_qa_corpus_campaign_complete_members
        AFTER INSERT OR UPDATE ON document_transform_qa_corpus_campaigns
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION recheck_qa_corpus_campaign();
        """
    )
    op.execute(  # sarj-noqa: SARJ470 — DB constraint trigger revalidates membership at transaction commit.
        """
        CREATE CONSTRAINT TRIGGER ck_qa_corpus_member_recheck_campaign
        AFTER INSERT OR UPDATE OR DELETE ON document_transform_qa_corpus_members
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION recheck_qa_corpus_campaign();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER ck_qa_corpus_submission_recheck_campaign
        AFTER INSERT OR UPDATE OR DELETE ON __QA_CORPUS_SUBMISSIONS__
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION recheck_qa_corpus_campaign();
        """.replace("__QA_CORPUS_SUBMISSIONS__", _SUBMISSIONS_TABLE)
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER ck_qa_corpus_submission_recheck_campaign ON {_SUBMISSIONS_TABLE}")
    op.execute(f"DROP TRIGGER guard_qa_corpus_submission_mutation ON {_SUBMISSIONS_TABLE}")
    op.execute(
        "DROP TRIGGER ck_qa_corpus_member_recheck_campaign ON document_transform_qa_corpus_members"
    )
    op.execute(
        "DROP TRIGGER guard_qa_corpus_member_mutation ON document_transform_qa_corpus_members"
    )
    op.execute(
        "DROP TRIGGER ck_qa_corpus_campaign_complete_members "
        "ON document_transform_qa_corpus_campaigns"
    )
    op.execute(
        "DROP TRIGGER guard_qa_corpus_campaign_update ON document_transform_qa_corpus_campaigns"
    )
    op.execute("DROP FUNCTION recheck_qa_corpus_campaign()")
    op.execute("DROP FUNCTION validate_qa_corpus_campaign(uuid)")
    op.execute("DROP FUNCTION guard_qa_corpus_submission_mutation()")
    op.execute("DROP FUNCTION guard_qa_corpus_member_mutation()")
    op.execute("DROP FUNCTION guard_qa_corpus_campaign_update()")
    op.drop_index(
        "uq_qa_corpus_campaign_active_resume",
        table_name="document_transform_qa_corpus_campaigns",
    )
    op.drop_index(
        "uq_qa_corpus_submission_output",
        table_name=_SUBMISSIONS_TABLE,
    )
    op.drop_index(
        "uq_qa_corpus_submission_task",
        table_name=_SUBMISSIONS_TABLE,
    )
    op.drop_table(_SUBMISSIONS_TABLE)
    op.drop_table("document_transform_qa_corpus_members")
    op.drop_index(
        "ix_qa_corpus_campaigns_binding",
        table_name="document_transform_qa_corpus_campaigns",
    )
    op.drop_index(
        "ix_qa_corpus_campaigns_created_at",
        table_name="document_transform_qa_corpus_campaigns",
    )
    op.drop_table("document_transform_qa_corpus_campaigns")
