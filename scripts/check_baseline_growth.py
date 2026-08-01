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


def main(*, base_path: str, current_path: str, allow_new_rules: bool = False) -> int:
    """Compare a baseline to the version on the pull request.

    With `allow_new_rules` (passed by CI only when the PR changes the
    Standards pin in .sarj-standards.toml), entries for rule ids absent from
    the entire base baseline are allowed: a toolchain upgrade adopts new rules
    whose pre-existing findings are absorbed by regeneration, then ratcheted.
    Without it, a rule at zero suppressions stays at zero.

    Returns:
        Nonzero when any current count exceeds its baseline count.

    """
    base = _load(base_path)
    current = _load(current_path)
    base_rules = {rule for _, rule in base}
    adopted = sorted({rule for _, rule in current if rule not in base_rules})
    growth: list[str] = []
    for (filename, rule), count in sorted(current.items()):
        if allow_new_rules and rule not in base_rules:
            continue
        old_count = base.get((filename, rule), 0)
        if count > old_count:
            growth.append(f"{filename}/{rule}: {old_count} -> {count}")
    if growth:
        sys.stderr.write("Lint baselines may only shrink:\n" + "\n".join(growth) + "\n")
        return 1
    if allow_new_rules and adopted:
        sys.stderr.write(
            "Standards upgrade: absorbed findings for newly adopted rules "
            "(shrink-only from here):\n" + "\n".join(adopted) + "\n"
        )
    return 0


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


def _load(path: str) -> dict[tuple[str, str], int]:
    baseline = _parse(path)
    return {
        (filename, rule): value if isinstance(value, int) else value.count
        for filename, rules in baseline.root.items()
        for rule, value in rules.items()
    }


def _parse(path: str) -> _Baseline:
    return _Baseline.model_validate_json(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    arguments = sys.argv[1:]
    allow_new = "--allow-new-rules" in arguments
    if allow_new:
        arguments = [argument for argument in arguments if argument != "--allow-new-rules"]
    if len(arguments) == _GROWTH_ARG_COUNT - 1:
        raise SystemExit(
            main(base_path=arguments[0], current_path=arguments[1], allow_new_rules=allow_new)
        )
    if len(arguments) == _INITIAL_ARG_COUNT - 1 and arguments[0] == "--initial":
        raise SystemExit(
            check_initial(
                baseline_path=arguments[1],
                changed_path=arguments[2],
                prefix=arguments[3],
            )
        )
    sys.stderr.write(
        "usage: check_baseline_growth.py [--allow-new-rules] BASE CURRENT\n"
        "   or: check_baseline_growth.py --initial BASELINE CHANGED_FILES PREFIX\n"
    )
    raise SystemExit(2)
