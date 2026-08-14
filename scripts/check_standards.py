"""Run every installed Sarj registry with the repository's shrink-only baseline."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from sarj_iac_lint.__main__ import main as iac_main
from sarj_iac_lint.rules import REGISTRY as IAC_RULES
from sarj_python_lint.__main__ import main as python_main
from sarj_python_lint.rules import REGISTRY as PYTHON_RULES
from sarj_sql_lint.__main__ import main as sql_main
from sarj_sql_lint.rules import REGISTRY as SQL_RULES


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


_BASELINE = Path(__file__).resolve().parents[1] / "python-standards-baseline.json"
_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "dist",
        "node_modules",
        "vendor",
    }
)


@dataclass
class _GroupedPaths:
    python: list[str] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)
    iac: list[str] = field(default_factory=list)


def main(paths: list[str]) -> int:
    grouped = group_paths(paths)
    statuses = [
        _run_python(grouped.python),
        _run_sql(grouped.sql),
        _run_iac(grouped.iac),
    ]
    return max(statuses)


def verify_python_baseline(paths: list[str]) -> int:
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


def update_python_baseline(paths: list[str]) -> int:
    return python_main(
        [
            "check",
            *_rule_arguments(PYTHON_RULES),
            "--update-baseline",
            str(_BASELINE),
            *group_paths(paths).python,
        ]
    )


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


def group_paths(paths: Sequence[str]) -> _GroupedPaths:
    grouped = _GroupedPaths()
    for raw_path in paths:
        path = Path(raw_path)
        candidates = path.rglob("*") if path.is_dir() else (path,)
        for candidate in candidates:
            if path.is_dir() and any(
                part in _IGNORED_DIRS for part in candidate.relative_to(path).parts
            ):
                continue
            if candidate.is_symlink():
                msg = f"refusing symlink input: {candidate}"
                raise ValueError(msg)
            if not candidate.is_file():
                continue
            suffix = candidate.suffix.lower()
            rendered = str(candidate) if path.is_dir() else raw_path
            if suffix == ".py":
                grouped.python.append(rendered)
            elif suffix == ".sql":
                grouped.sql.append(rendered)
            elif suffix in {".hcl", ".tf", ".tfvars", ".yaml", ".yml"}:
                grouped.iac.append(rendered)
    return grouped


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["--update-python-baseline"]:
        raise SystemExit(update_python_baseline(arguments[1:] or ["."]))
    if arguments[:1] == ["--verify-python-baseline"]:
        raise SystemExit(verify_python_baseline(arguments[1:] or ["."]))
    raise SystemExit(main(arguments or ["."]))
