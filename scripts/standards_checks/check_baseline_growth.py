from __future__ import annotations

from pathlib import Path
import re
import sys
from typing import Annotated, Literal, NamedTuple, Never

from pydantic import BaseModel, Field, RootModel
import typer


class _Count(BaseModel):
    count: int = Field(gt=0)


class _FindingKey(NamedTuple):
    filename: str
    rule: str


_PositiveInt = Annotated[int, Field(gt=0)]
_VERSION_RE = re.compile(r'^(?:bundle|version)\s*=\s*"([^"]+)"', re.MULTILINE)
CLI = typer.Typer(add_completion=False, add_help_option=False, pretty_exceptions_enable=False)


class _Baseline(RootModel[dict[str, dict[str, _PositiveInt | _Count]]]):
    pass


class _BootstrapGrowth(BaseModel):
    before: int = Field(ge=0)
    after: int = Field(gt=0)


class _Bootstrap(BaseModel):
    schema_version: Literal[1]
    merge_base: str = Field(pattern=r"^[0-9a-f]{40}$")
    from_bundle: str = Field(min_length=1)
    to_bundle: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    allowances: dict[str, dict[str, dict[str, _BootstrapGrowth]]]


def main(
    *,
    base_path: str,
    current_path: str,
    standards_base: str | None = None,
    standards_current: str | None = None,
    base_config: str | None = None,
    bootstrap: str | None = None,
    merge_base: str | None = None,
    allowance_path: str | None = None,
    changed_files: str | None = None,
    changed_prefix: str | None = None,
) -> int:
    base = _load(base_path)
    current = _load(current_path)
    allow_new_rules = _versions_differ(standards_base, standards_current)
    base_config_text = Path(base_config).read_text(encoding="utf-8") if base_config else None
    base_rules = {rule for _, rule in base}
    declared = _declared_rebaseline(
        bootstrap=bootstrap,
        merge_base=merge_base,
        allowance_path=allowance_path,
        changed_files=changed_files,
        changed_prefix=changed_prefix,
        standards_base=standards_base,
        standards_current=standards_current,
    )
    growth: list[str] = []
    exempted: set[str] = set()
    used_declarations: set[_FindingKey] = set()
    for (filename, rule), count in sorted(current.items()):
        key = _FindingKey(filename, rule)
        old_count = base.get(key, 0)
        declared_growth = declared.get(key)
        if declared_growth is not None:
            if old_count == declared_growth.before and count == declared_growth.after:
                used_declarations.add(key)
                continue
            growth.append(
                f"{filename}/{rule}: declared {declared_growth.before} -> "
                f"{declared_growth.after}, observed {old_count} -> {count}"
            )
            continue
        if rule not in base_rules and _adopted_at_bump(
            rule, allow_new_rules=allow_new_rules, base_config_text=base_config_text
        ):
            exempted.add(rule)
            continue
        if count > old_count:
            growth.append(f"{filename}/{rule}: {old_count} -> {count}")
    unused_declarations = sorted(set(declared) - used_declarations)
    if unused_declarations:
        growth.extend(
            f"{key.filename}/{key.rule}: declared re-baseline is stale or padded"
            for key in unused_declarations
        )
    if growth:
        sys.stderr.write("Lint baselines may only shrink:\n" + "\n".join(growth) + "\n")
        return 1
    if exempted:
        sys.stderr.write(
            "Standards upgrade (pin version changed): absorbed findings for newly "
            "adopted rules (shrink-only from here):\n" + "\n".join(sorted(exempted)) + "\n"
        )
    return 0


def _declared_rebaseline(
    *,
    bootstrap: str | None,
    merge_base: str | None,
    allowance_path: str | None,
    changed_files: str | None,
    changed_prefix: str | None,
    standards_base: str | None,
    standards_current: str | None,
) -> dict[_FindingKey, _BootstrapGrowth]:
    arguments = (merge_base, allowance_path, changed_files, changed_prefix)
    if bootstrap is None:
        if any(argument is not None for argument in arguments):
            _abort("re-baseline options require --bootstrap")
        return {}
    if (
        merge_base is None
        or allowance_path is None
        or changed_files is None
        or changed_prefix is None
    ):
        _abort(
            "--bootstrap requires --merge-base, --allowance-path, --changed-files, "
            "and --changed-prefix"
        )
    if standards_base is None or standards_current is None:
        _abort("--bootstrap requires both Standards manifest paths")

    declaration = _Bootstrap.model_validate_json(Path(bootstrap).read_text(encoding="utf-8"))
    if declaration.merge_base != merge_base:
        sys.stderr.write(
            f"{bootstrap}: declaration is inert for merge-base {merge_base}; "
            f"it is pinned to {declaration.merge_base}\n"
        )
        return {}
    base_bundle = _pin_version(standards_base)
    current_bundle = _pin_version(standards_current)
    if (declaration.from_bundle, declaration.to_bundle) != (
        base_bundle,
        current_bundle,
    ):
        _abort(
            f"{bootstrap}: declared bundle transition "
            f"{declaration.from_bundle} -> {declaration.to_bundle}, observed "
            f"{base_bundle} -> {current_bundle}"
        )
    if set(declaration.allowances) != {allowance_path}:
        _abort(f"{bootstrap}: allowances must name exactly {allowance_path!r}")

    return _validated_allowances(
        declaration.allowances[allowance_path],
        bootstrap=bootstrap,
        changed=set(Path(changed_files).read_text(encoding="utf-8").splitlines()),
        changed_prefix=changed_prefix,
    )


def _validated_allowances(
    allowances: dict[str, dict[str, _BootstrapGrowth]],
    *,
    bootstrap: str,
    changed: set[str],
    changed_prefix: str,
) -> dict[_FindingKey, _BootstrapGrowth]:
    declared: dict[_FindingKey, _BootstrapGrowth] = {}
    for filename, rules in allowances.items():
        if f"{changed_prefix}{filename}" in changed:
            _abort(f"{bootstrap}: cannot re-baseline changed source {changed_prefix}{filename}")
        for rule, counts in rules.items():
            if counts.after <= counts.before:
                _abort(f"{bootstrap}: {filename}/{rule} must declare actual growth")
            declared[_FindingKey(filename, rule)] = counts
    if not declared:
        _abort(f"{bootstrap}: declaration has no exact allowances")
    return declared


def _abort(message: str) -> Never:
    sys.stderr.write(f"{message}\n")
    raise SystemExit(2)


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


def _adopted_at_bump(rule: str, *, allow_new_rules: bool, base_config_text: str | None) -> bool:
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
    return _Baseline.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _usage() -> Never:
    sys.stderr.write(
        "usage: check_baseline_growth.py [--standards-base FILE --standards-current FILE]\n"
        "                                [--base-config FILE]\n"
        "                                [--bootstrap FILE --merge-base SHA\n"
        "                                 --allowance-path PATH --changed-files FILE\n"
        "                                 --changed-prefix PREFIX] BASE CURRENT\n"
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
    bootstrap: Annotated[str | None, typer.Option()] = None,
    merge_base: Annotated[str | None, typer.Option()] = None,
    allowance_path: Annotated[str | None, typer.Option()] = None,
    changed_files: Annotated[str | None, typer.Option()] = None,
    changed_prefix: Annotated[str | None, typer.Option()] = None,
) -> None:
    if initial:
        if initial_prefix is None or any(
            value is not None
            for value in (
                standards_base,
                standards_current,
                base_config,
                bootstrap,
                merge_base,
                allowance_path,
                changed_files,
                changed_prefix,
            )
        ):
            _usage()
        raise SystemExit(
            check_initial(baseline_path=base_path, changed_path=current_path, prefix=initial_prefix)
        )
    if initial_prefix is not None:
        _usage()
    raise SystemExit(
        main(
            base_path=base_path,
            current_path=current_path,
            standards_base=standards_base,
            standards_current=standards_current,
            base_config=base_config,
            bootstrap=bootstrap,
            merge_base=merge_base,
            allowance_path=allowance_path,
            changed_files=changed_files,
            changed_prefix=changed_prefix,
        )
    )


def cli() -> None:
    try:
        CLI(standalone_mode=False)
    except typer.TyperException:
        _usage()


if __name__ == "__main__":
    cli()
