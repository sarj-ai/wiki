"""Reject increases in nested integer lint-baseline counts."""

from __future__ import annotations

from pathlib import Path
import sys

from pydantic import BaseModel, RootModel


class _Count(BaseModel):
    count: int


class _Baseline(RootModel[dict[str, dict[str, int | _Count]]]):
    pass


def main(*, base_path: str, current_path: str) -> int:
    """Return nonzero when any current count exceeds its baseline count."""
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


def _load(path: str) -> dict[str, int]:
    baseline = _Baseline.model_validate_json(Path(path).read_text(encoding="utf-8"))
    return {
        f"{filename}/{rule}": value if isinstance(value, int) else value.count
        for filename, rules in baseline.root.items()
        for rule, value in rules.items()
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.stderr.write("usage: check_baseline_growth.py BASE CURRENT\n")
        raise SystemExit(2)
    raise SystemExit(main(base_path=sys.argv[1], current_path=sys.argv[2]))
