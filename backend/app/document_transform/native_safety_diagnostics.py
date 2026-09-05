from typing import Literal, NamedTuple, Never

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


FooterShapeCheck = Literal[
    "footer_container",
    "footer_fields",
    "content",
    "paragraph_count",
    "paragraph_container",
    "paragraph_fields",
    "runs",
    "run_container",
    "run_fields",
    "text_run",
    "text_run_fields",
    "text_content",
    "caption",
    "text_style",
    "link",
    "font_family",
    "font_size",
    "bold",
    "italic",
    "alignment",
    "named_style",
]


class FooterShapeFailure(NamedTuple):
    check: FooterShapeCheck
    reason: Literal["missing", "mismatch"]


def native_safety_error(
    stage: SafetyStage,
    reason: SafetyReason,
    *,
    footer_shape: FooterShapeFailure | None = None,
) -> Never:
    # Static caller vocabulary only: no provider content, identifiers or hashes.
    # Raising the existing error preserves every caller's fail-closed policy.
    logger.warning(
        "document native safety stop stage={safety_stage} reason={safety_reason}",
        safety_stage=stage,
        safety_reason=reason,
        **(
            {"footer_shape_check": footer_shape.check, "footer_shape_reason": footer_shape.reason}
            if footer_shape is not None
            else {}
        ),
    )
    raise InPlaceNeedsReviewError
