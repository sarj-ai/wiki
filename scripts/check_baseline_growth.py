"""Reject increases in nested integer lint-baseline counts."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Annotated

from pydantic import BaseModel, Field, RootModel


class _Count(BaseModel):
    count: int = Field(gt=0)


_PositiveInt = Annotated[int, Field(gt=0)]
_VERSION_RE = re.compile(r'^(?:bundle|version)\s*=\s*"([^"]+)"', re.MULTILINE)
_GROWTH_POSITIONAL_COUNT = 2
_INITIAL_ARG_COUNT = 4


class _Baseline(RootModel[dict[str, dict[str, _PositiveInt | _Count]]]):
    pass


def main(
    *,
    base_path: str,
    current_path: str,
    standards_base: str | None = None,
    standards_current: str | None = None,
    base_config: str | None = None,
) -> int:
    base = _load(base_path)
    current = _load(current_path)
    allow_new_rules = _versions_differ(standards_base, standards_current)
    base_config_text = Path(base_config).read_text(encoding="utf-8") if base_config else None
    base_rules = {rule for _, rule in base}
    growth: list[str] = []
    exempted: set[str] = set()
    for (filename, rule), count in sorted(current.items()):
        if rule not in base_rules and _adopted_at_bump(
            rule, allow_new_rules=allow_new_rules, base_config_text=base_config_text
        ):
            exempted.add(rule)
            continue
        old_count = base.get((filename, rule), 0)
        if count > old_count:
            growth.append(f"{filename}/{rule}: {old_count} -> {count}")
    if growth:
        sys.stderr.write("Lint baselines may only shrink:\n" + "\n".join(growth) + "\n")
        return 1
    if exempted:
        sys.stderr.write(
            "Standards upgrade (pin version changed): absorbed findings for newly "
            "adopted rules (shrink-only from here):\n" + "\n".join(sorted(exempted)) + "\n"
        )
    return 0


def check_initial(*, baseline_path: str, changed_path: str, prefix: str) -> int:
    baseline = _parse(baseline_path)
    changed = set(Path(changed_path).read_text(encoding="utf-8").splitlines())
    exempted_changed = sorted(
        f"{prefix}{filename}" for filename in baseline.root if f"{prefix}{filename}" in changed
    )
    if not exempted_changed:
        return 0
    sys.stderr.write(
        "An initial baseline cannot exempt files changed by this PR:\n"
        + "\n".join(exempted_changed)
        + "\nFix the new violations instead of baselining them.\n"
    )
    return 1


def _adopted_at_bump(
    rule: str, *, allow_new_rules: bool, base_config_text: str | None
) -> bool:
    if not allow_new_rules:
        return False
    if base_config_text is None:
        return True
    return re.search(rf"(?<![\w/@-]){re.escape(rule)}(?![\w/-])", base_config_text) is None


def _versions_differ(standards_base: str | None, standards_current: str | None) -> bool:
    if standards_base is None or standards_current is None:
        return False
    return _pin_version(standards_base) != _pin_version(standards_current)


def _pin_version(path: str) -> str:
    match = _VERSION_RE.search(Path(path).read_text(encoding="utf-8"))
    if match is None:
        sys.stderr.write(f"{path}: no `bundle = \"...\"` or `version = \"...\"` key found\n")
        raise SystemExit(2)
    return match.group(1)


def _load(path: str) -> dict[tuple[str, str], int]:
    baseline = _parse(path)
    return {
        (filename, rule): value if isinstance(value, int) else value.count
        for filename, rules in baseline.root.items()
        for rule, value in rules.items()
    }


def _parse(path: str) -> _Baseline:
    return _Baseline.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _pop_option(arguments: list[str], name: str) -> str | None:
    if name not in arguments:
        return None
    index = arguments.index(name)
    if index + 1 >= len(arguments):
        _usage()
    arguments.pop(index)
    return arguments.pop(index)


def _usage() -> None:
    sys.stderr.write(
        "usage: check_baseline_growth.py [--standards-base FILE --standards-current FILE]\n"
        "                                [--base-config FILE] BASE CURRENT\n"
        "   or: check_baseline_growth.py --initial BASELINE CHANGED_FILES PREFIX\n"
    )
    raise SystemExit(2)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) == _INITIAL_ARG_COUNT and argv[0] == "--initial":
        raise SystemExit(
            check_initial(baseline_path=argv[1], changed_path=argv[2], prefix=argv[3])
        )
    standards_base_arg = _pop_option(argv, "--standards-base")
    standards_current_arg = _pop_option(argv, "--standards-current")
    base_config_arg = _pop_option(argv, "--base-config")
    if len(argv) != _GROWTH_POSITIONAL_COUNT:
        _usage()
    raise SystemExit(
        main(
            base_path=argv[0],
            current_path=argv[1],
            standards_base=standards_base_arg,
            standards_current=standards_current_arg,
            base_config=base_config_arg,
        )
    )
