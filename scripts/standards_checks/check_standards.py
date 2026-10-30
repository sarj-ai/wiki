from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Annotated

from sarj_iac_lint.__main__ import main as iac_main
from sarj_iac_lint.rules import REGISTRY as IAC_RULES
from sarj_python_lint.__main__ import main as python_main
from sarj_python_lint.rules import REGISTRY as PYTHON_RULES
from sarj_sql_lint.__main__ import main as sql_main
from sarj_sql_lint.rules import REGISTRY as SQL_RULES
import typer


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


_BASELINE = Path(__file__).resolve().parents[2] / "python-standards-baseline.json"
CLI = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
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
                "--jobs",
                os.environ.get("WIKI_PYTHON_BASELINE_JOBS", "1"),  # ruff: ignore[banned-api] -- standalone CLI forwards the native analysis budget.
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


def _rule_arguments(registry: Iterable[str]) -> list[str]:
    return [part for rule in sorted(registry) for part in ("--rule", rule)]


def group_paths(paths: Sequence[str]) -> _GroupedPaths:
    grouped = _GroupedPaths()
    for raw_path in paths:
        path = Path(raw_path)
        is_directory = path.is_dir()
        candidates = _source_files(path) if is_directory else (path,)
        for candidate in candidates:
            if candidate.is_symlink():
                msg = f"refusing symlink input: {candidate}"
                raise ValueError(msg)
            if not candidate.is_file():
                continue
            suffix = candidate.suffix.lower()
            rendered = str(candidate) if is_directory else raw_path
            if suffix == ".py":
                grouped.python.append(rendered)
            elif suffix == ".sql":
                grouped.sql.append(rendered)
            elif suffix in {".hcl", ".tf", ".tfvars", ".yaml", ".yml"}:
                grouped.iac.append(rendered)
    return grouped


def _source_files(root: Path) -> Iterator[Path]:
    for directory, subdirectories, filenames in root.walk():
        subdirectories[:] = sorted(name for name in subdirectories if name not in _IGNORED_DIRS)
        for name in sorted(filenames):
            if name not in _IGNORED_DIRS:
                yield directory / name


@CLI.command()
def _check_command(
    paths: Annotated[list[str] | None, typer.Argument()] = None,
    *,
    update_baseline: Annotated[bool, typer.Option("--update-python-baseline")] = False,
    verify_baseline: Annotated[bool, typer.Option("--verify-python-baseline")] = False,
) -> None:
    if update_baseline and verify_baseline:
        message = "Choose one Python baseline operation."
        raise typer.BadParameter(message)
    arguments = paths or ["."]
    if update_baseline:
        raise SystemExit(update_python_baseline(arguments))
    if verify_baseline:
        raise SystemExit(verify_python_baseline(arguments))
    raise SystemExit(main(arguments))


if __name__ == "__main__":
    CLI()
