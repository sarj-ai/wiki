"""Run every installed Sarj registry with the repository's shrink-only baseline."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from sarj_iac_lint.__main__ import main as iac_main
from sarj_iac_lint.rules import REGISTRY as IAC_RULES
from sarj_lint_configs.runner import group_paths
from sarj_python_lint.__main__ import main as python_main
from sarj_python_lint.rules import REGISTRY as PYTHON_RULES
from sarj_sql_lint.__main__ import main as sql_main
from sarj_sql_lint.rules import REGISTRY as SQL_RULES


if TYPE_CHECKING:
    from collections.abc import Mapping


_BASELINE = Path(__file__).resolve().parents[1] / "python-standards-baseline.json"


def main(paths: list[str]) -> int:
    """Dispatch paths to all current registries without a handwritten rule list.

    Returns:
        The highest exit status returned by an applicable registry.

    """
    grouped = group_paths(paths)
    statuses = [
        _run_python(grouped.python),
        _run_sql(grouped.sql),
        _run_iac(grouped.iac),
    ]
    return max(statuses)


def verify_python_baseline(paths: list[str]) -> int:
    """Compare the Python baseline with independently generated diagnostics.

    Returns:
        Nonzero when the committed baseline is stale or inflated.

    """
    python_paths = group_paths(paths).python
    with TemporaryDirectory(prefix="wiki-python-baseline-") as directory:
        generated = Path(directory) / "baseline.json"
        status = python_main(
            [
                "check",
                *_rule_arguments(PYTHON_RULES),
                "--update-baseline",
                str(generated),
                *python_paths,
            ]
        )
        if status:
            return status
        committed_data = json.loads(_BASELINE.read_text(encoding="utf-8"))
        generated_data = json.loads(generated.read_text(encoding="utf-8"))
    if committed_data == generated_data:
        return 0
    sys.stderr.write(
        "python-standards-baseline.json is stale or inflated. "
        "Regenerate it from current diagnostics and only shrink existing allowances.\n"
    )
    return 1


def _run_python(paths: list[str]) -> int:
    if not paths:
        return 0
    return python_main(
        [
            "check",
            *_rule_arguments(PYTHON_RULES),
            "--baseline",
            str(_BASELINE),
            *paths,
        ]
    )


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
    arguments = sys.argv[1:]
    if arguments[:1] == ["--verify-python-baseline"]:
        raise SystemExit(verify_python_baseline(arguments[1:] or ["."]))
    raise SystemExit(main(arguments or ["."]))
