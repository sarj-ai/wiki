from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Protocol, runtime_checkable


_MIGRATION = (
    Path(__file__).parents[1] / "alembic" / "versions" / "0020_document_transform_qa_attestation.py"
)
_STATE_CHECK = "ck_document_transform_qa_run_state"
_ATOMIC_CHECK = "ck_document_transform_qa_run_atomic_state"


class _MigrationLoadError(RuntimeError):
    pass


@runtime_checkable
class _Migration(Protocol):
    op: object

    def upgrade(self) -> None: ...


class _Populated0019Operations:
    def __init__(self) -> None:
        self.run_ids = ["created-run", "bound-run", "bootstrapped-run"]
        self.states = ["created", "execution_bound", "bootstrapped"]
        self.grants_revoked = [False, False, False]
        self.dropped_checks: set[str] = set()

    def add_column(self, *_args: object, **_kwargs: object) -> None:
        return None

    def drop_constraint(self, name: str, *_args: object, **_kwargs: object) -> None:
        self.dropped_checks.add(name)

    def execute(self, statement: str) -> None:
        if "UPDATE document_transform_qa_grants" in statement:
            self.grants_revoked = [True for _ in self.grants_revoked]
            return
        if "UPDATE document_transform_qa_runs SET state = 'retired'" in statement:
            assert {_STATE_CHECK, _ATOMIC_CHECK} <= self.dropped_checks
            self.states = ["retired" for _ in self.states]
            return
        raise AssertionError

    def create_unique_constraint(self, *_args: object, **_kwargs: object) -> None:
        return None

    def create_index(self, *_args: object, **_kwargs: object) -> None:
        return None

    def create_check_constraint(self, *_args: object, **_kwargs: object) -> None:
        assert self.states == ["retired", "retired", "retired"]
        assert all(self.grants_revoked)


def _load_migration() -> _Migration:
    spec = importlib.util.spec_from_file_location("qa_attestation_migration", _MIGRATION)
    if spec is None or spec.loader is None:
        raise _MigrationLoadError
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not isinstance(module, _Migration):
        raise _MigrationLoadError
    return module


def test_populated_0019_upgrade_retires_runs_and_revokes_grants_before_new_checks() -> None:
    migration = _load_migration()
    operations = _Populated0019Operations()
    migration.op = operations

    migration.upgrade()

    assert operations.run_ids == ["created-run", "bound-run", "bootstrapped-run"]
    assert operations.states == ["retired", "retired", "retired"]
    assert all(operations.grants_revoked)
