from typing import Literal, Never

from loguru import logger

from app.document_transform.errors import InPlaceNeedsReviewError


SafetyStage = Literal[
    "footer_plan",
    "footer_create",
    "footer_populate",
    "footer_advance",
    "footer_coverage",
    "footer_projection_empty",
    "footer_projection_populated",
    "footer_tab",
    "tab_naming_plan",
    "tab_naming_pre",
    "tab_naming_post",
    "date_plan",
    "date_pre",
]
SafetyReason = Literal[
    "revision",
    "topology",
    "marker",
    "segment",
    "shape",
    "invariant",
    "action",
    "preimage",
]


def native_safety_error(stage: SafetyStage, reason: SafetyReason) -> Never:
    # Static caller vocabulary only: no provider content, identifiers or hashes.
    # Raising the existing error preserves every caller's fail-closed policy.
    logger.warning(
        "document native safety stop stage={safety_stage} reason={safety_reason}",
        safety_stage=stage,
        safety_reason=reason,
    )
    raise InPlaceNeedsReviewError
