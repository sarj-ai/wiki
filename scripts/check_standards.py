"""Run every installed Sarj registry with the repository's shrink-only baseline."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys

from sarj_iac_lint.__main__ import main as iac_main
from sarj_iac_lint.rules import REGISTRY as IAC_RULES
from sarj_lint_configs.runner import group_paths
from sarj_python_lint.__main__ import main as python_main
from sarj_python_lint.rules import REGISTRY as PYTHON_RULES
from sarj_sql_lint.__main__ import main as sql_main
from sarj_sql_lint.rules import REGISTRY as SQL_RULES


_BASELINE = Path(__file__).resolve().parents[1] / "python-standards-baseline.json"


def main(paths: list[str]) -> int:
    """Dispatch paths to all current registries without a handwritten rule list."""
    grouped = group_paths(paths)
    statuses = [
        _run_python(grouped.python),
        _run_sql(grouped.sql),
        _run_iac(grouped.iac),
    ]
    return max(statuses)


def _run_python(paths: list[str]) -> int:
    if not paths:
        return 0
    return python_main([
        "check",
        *_rule_arguments(PYTHON_RULES),
        "--baseline",
        str(_BASELINE),
        *paths,
    ])


def _run_sql(paths: list[str]) -> int:
    if not paths:
        return 0
    return sql_main(["check", *_rule_arguments(SQL_RULES), *paths])


def _run_iac(paths: list[str]) -> int:
    if not paths:
        return 0
    return iac_main(["check", *_rule_arguments(IAC_RULES), *paths])


def _rule_arguments(registry: Mapping[str, object]) -> list[str]:
    return [part for rule in sorted(registry) for part in ("--rule", rule)]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["."]))
