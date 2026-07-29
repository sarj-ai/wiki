"""Reject increases in nested integer lint-baseline counts."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Annotated

from pydantic import BaseModel, Field, RootModel


class _Count(BaseModel):
    count: int = Field(gt=0)


_PositiveInt = Annotated[int, Field(gt=0)]
_GROWTH_ARG_COUNT = 3
_INITIAL_ARG_COUNT = 5


class _Baseline(RootModel[dict[str, dict[str, _PositiveInt | _Count]]]):
    pass


def main(*, base_path: str, current_path: str) -> int:
    """Compare a baseline to the version on the pull request.

    Returns:
        Nonzero when any current count exceeds its baseline count.

    """
    base = _load(base_path)
    current = _load(current_path)
    growth: list[str] = []
    for key, count in sorted(current.items()):
        old_count = base.get(key, 0)
        if count > old_count:
            growth.append(f"{key}: {old_count} -> {count}")
    if not growth:
        return 0
    sys.stderr.write("Lint baselines may only shrink:\n" + "\n".join(growth) + "\n")
    return 1


def check_initial(*, baseline_path: str, changed_path: str, prefix: str) -> int:
    """Reject initial exemptions for files changed by the introducing PR.

    Returns:
        Nonzero when a changed file receives an initial exemption.

    """
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


def _load(path: str) -> dict[str, int]:
    baseline = _parse(path)
    return {
        f"{filename}/{rule}": value if isinstance(value, int) else value.count
        for filename, rules in baseline.root.items()
        for rule, value in rules.items()
    }


def _parse(path: str) -> _Baseline:
    return _Baseline.model_validate_json(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    if len(sys.argv) == _GROWTH_ARG_COUNT:
        raise SystemExit(main(base_path=sys.argv[1], current_path=sys.argv[2]))
    if len(sys.argv) == _INITIAL_ARG_COUNT and sys.argv[1] == "--initial":
        raise SystemExit(
            check_initial(
                baseline_path=sys.argv[2],
                changed_path=sys.argv[3],
                prefix=sys.argv[4],
            )
        )
    sys.stderr.write(
        "usage: check_baseline_growth.py BASE CURRENT\n"
        "   or: check_baseline_growth.py --initial BASELINE CHANGED_FILES PREFIX\n"
    )
    raise SystemExit(2)
