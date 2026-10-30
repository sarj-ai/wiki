from collections.abc import Iterator
from typing import Literal

from loguru import logger


MAX_VISITED = 50_000
BulletStage = Literal[
    "source_read", "draft_read", "pre_header", "post_header", "pre_style", "post_style"
]


def bullet_stage_counts(document: object) -> dict[str, int | bool]:
    # The same whole native document scope is used at every boundary, including
    # child tabs and furniture. No strings, IDs, styles or content are emitted.
    counts: dict[str, int | bool] = {"bullet_snapshot_complete": True}
    for field in ("bold", "italic", "underline"):
        for state in ("true", "false", "missing", "malformed"):
            counts[f"bullet_snapshot_{field}_{state}"] = 0
    stack: list[Iterator[object]] = [iter((document,))]
    visited = 0
    while stack:
        value = next(stack[-1], _END)
        if value is _END:
            stack.pop()
            continue
        visited += 1
        if visited > MAX_VISITED:
            counts["bullet_snapshot_complete"] = False
            break
        match value:
            case dict():
                paragraph = value.get("paragraph")
                if isinstance(paragraph, dict) and "bullet" in paragraph:
                    bullet = paragraph.get("bullet")
                    style = bullet.get("textStyle", {}) if isinstance(bullet, dict) else None
                    for field in ("bold", "italic", "underline"):
                        if not isinstance(style, dict):
                            state = "malformed"
                        elif field not in style:
                            state = "missing"
                        elif style.get(field) is True:
                            state = "true"
                        elif style.get(field) is False:
                            state = "false"
                        else:
                            state = "malformed"
                        key = f"bullet_snapshot_{field}_{state}"
                        counts[key] += 1
                stack.append(iter(value.values()))
            case list():
                stack.append(iter(value))
            case _:
                pass
    return counts


_END = object()


def log_bullet_stage(document: object, stage: BulletStage) -> None:
    logger.info(
        "document bullet stage snapshot stage={bullet_stage} diagnostics={diagnostics}",
        bullet_stage=stage,
        diagnostics=bullet_stage_counts(document),
    )
