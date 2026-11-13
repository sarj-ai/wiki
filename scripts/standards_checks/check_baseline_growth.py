from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Annotated, ClassVar, Literal, NamedTuple, Never, assert_never

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError
import typer


class _Count(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="forbid")
    count: int = Field(gt=0)


class _FindingKey(NamedTuple):
    filename: str
    rule: str


_PositiveInt = Annotated[int, Field(strict=True, gt=0)]
_VERSION_RE = re.compile(r'^(?:bundle|version)\s*=\s*"([^"]+)"', re.MULTILINE)
CLI = typer.Typer(add_completion=False, add_help_option=False, pretty_exceptions_enable=False)


class _Baseline(RootModel[dict[str, dict[str, _PositiveInt | _Count]]]):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True)


@dataclass(frozen=True, slots=True)
class ExistingRules:
    kind: ClassVar[Literal["existing"]] = "existing"


@dataclass(frozen=True, slots=True)
class AdoptedRules:
    base_config: str | None
    kind: ClassVar[Literal["adopted"]] = "adopted"


type RulePolicy = ExistingRules | AdoptedRules


@dataclass(frozen=True, slots=True)
class InitialBaseline:
    baseline_path: str
    changed_path: str
    prefix: str
    kind: ClassVar[Literal["initial"]] = "initial"


@dataclass(frozen=True, slots=True)
class CompareBaselines:
    base_path: str
    current_path: str
    rules: RulePolicy
    kind: ClassVar[Literal["compare"]] = "compare"


type BaselineCommand = InitialBaseline | CompareBaselines


def execute(command: BaselineCommand) -> int:
    match command:
        case InitialBaseline():
            return check_initial(command)
        case CompareBaselines():
            return compare_baselines(command)
        case _:
            assert_never(command)


def compare_baselines(command: CompareBaselines) -> int:
    base = _load(command.base_path)
    current = _load(command.current_path)
    base_rules = {rule for _, rule in base}
    growth: list[str] = []
    exempted: set[str] = set()
    for (filename, rule), count in sorted(current.items()):
        key = _FindingKey(filename, rule)
        old_count = base.get(key, 0)
        if rule not in base_rules and _adopted_rule(rule, command.rules):
            exempted.add(rule)
            continue
        if count > old_count:
            growth.append(f"{filename}/{rule}: {old_count} -> {count}")
    if growth:
        sys.stderr.write("Lint baselines may only shrink:\n" + "\n".join(growth) + "\n")
        return 1
    if exempted:
        sys.stderr.write(
            "Standards upgrade (pin version changed): absorbed findings for newly adopted rules (shrink-only from here):\n"
            + "\n".join(sorted(exempted))
            + "\n"
        )
    return 0


def check_initial(command: InitialBaseline) -> int:
    baseline = _parse(command.baseline_path)
    changed = set(Path(command.changed_path).read_text(encoding="utf-8").splitlines())
    exempted_changed = sorted(
        f"{command.prefix}{filename}"
        for filename in baseline.root
        if f"{command.prefix}{filename}" in changed
    )
    if not exempted_changed:
        return 0
    sys.stderr.write(
        "An initial baseline cannot exempt files changed by this PR:\n"
        + "\n".join(exempted_changed)
        + "\nFix the new violations instead of baselining them.\n"
    )
    return 1


def _adopted_rule(rule: str, policy: RulePolicy) -> bool:
    match policy:
        case ExistingRules():
            return False
        case AdoptedRules(base_config=config):
            return (
                config is None
                or re.search(rf"(?<![\w/@-]){re.escape(rule)}(?![\w/-])", config) is None
            )
        case _:
            assert_never(policy)


def _pin_version(path: str) -> str:
    match = _VERSION_RE.search(Path(path).read_text(encoding="utf-8"))
    if match is None:
        sys.stderr.write(f'{path}: no `bundle = "..."` or `version = "..."` key found\n')
        raise SystemExit(2)
    return match.group(1)


def _load(path: str) -> dict[_FindingKey, int]:
    baseline = _parse(path)
    return {
        _FindingKey(filename, rule): value if isinstance(value, int) else value.count
        for filename, rules in baseline.root.items()
        for rule, value in rules.items()
    }


def _parse(path: str) -> _Baseline:
    try:
        return _Baseline.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except ValidationError as error:
        sys.stderr.write(f"{path}: invalid baseline counts\n")
        raise SystemExit(2) from error


def _usage() -> Never:
    sys.stderr.write(
        "usage: check_baseline_growth.py [--standards-base FILE --standards-current FILE]\n"
        "                                [--base-config FILE] BASE CURRENT\n"
        "   or: check_baseline_growth.py --initial BASELINE CHANGED_FILES PREFIX\n"
    )
    raise SystemExit(2)


@CLI.command(add_help_option=False)
def _check_command(
    base_path: Annotated[str, typer.Argument()],
    current_path: Annotated[str, typer.Argument()],
    initial_prefix: Annotated[str | None, typer.Argument()] = None,
    *,
    initial: Annotated[bool, typer.Option("--initial")] = False,
    standards_base: Annotated[str | None, typer.Option()] = None,
    standards_current: Annotated[str | None, typer.Option()] = None,
    base_config: Annotated[str | None, typer.Option()] = None,
) -> None:
    if initial:
        if initial_prefix is None or any(
            value is not None for value in (standards_base, standards_current, base_config)
        ):
            _usage()
        raise SystemExit(execute(InitialBaseline(base_path, current_path, initial_prefix)))
    if initial_prefix is not None or (standards_base is None) != (standards_current is None):
        _usage()
    rules: RulePolicy = ExistingRules()
    if standards_base is not None and standards_current is not None:
        if _pin_version(standards_base) != _pin_version(standards_current):
            rules = AdoptedRules(
                Path(base_config).read_text(encoding="utf-8") if base_config else None
            )
    elif base_config is not None:
        _usage()
    raise SystemExit(execute(CompareBaselines(base_path, current_path, rules)))


def cli() -> None:
    try:
        CLI(standalone_mode=False)
    except typer.TyperException:
        _usage()


if __name__ == "__main__":
    cli()
