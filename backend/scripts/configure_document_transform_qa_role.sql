\if :{?qa_role}
\else
\echo 'qa_role must be set to the exact Cloud SQL IAM database user'
\quit 1
\endif

BEGIN;

GRANT USAGE ON SCHEMA public TO :"qa_role";
GRANT SELECT ON users, user_sessions TO :"qa_role";
GRANT UPDATE (
  encrypted_access_token,
  encrypted_refresh_token,
  google_access_token_expires_at,
  google_token_generation,
  google_refresh_lease_owner,
  google_refresh_lease_until
) ON users TO :"qa_role";
GRANT SELECT ON document_transform_qa_grants TO :"qa_role";
GRANT UPDATE (consumed_at, consumed_by_execution)
  ON document_transform_qa_grants TO :"qa_role";

COMMIT;
