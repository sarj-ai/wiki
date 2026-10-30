class DocumentTransformError(Exception):
    code = "document_transform_failed"
    public_detail = "The document could not be transformed safely"


class SourceChangedError(DocumentTransformError):
    code = "source_changed"
    public_detail = "The source changed after the transformation was requested"


class DestinationAccessChangedError(DocumentTransformError):
    code = "destination_access_changed"
    public_detail = "The copy destination access changed after the transformation was requested"


class SourceTooLargeError(DocumentTransformError):
    code = "source_too_large"
    public_detail = "The source exceeds the document transformation limit"


class UnsupportedSourceError(DocumentTransformError):
    code = "unsupported_source"
    public_detail = "This source cannot be transformed without losing content"


class EmptyMarkdownBlockError(UnsupportedSourceError):
    public_detail = (
        "Empty Markdown headings and code blocks cannot be verified after import; "
        "add text or remove the empty block"
    )


class OutputConflictError(DocumentTransformError):
    code = "output_conflict"
    public_detail = "The existing output belongs to a different source revision"


class OutputVerificationError(DocumentTransformError):
    code = "output_verification_failed"
    public_detail = "The transformed output did not pass lossless verification"


class IncompatiblePendingOutputError(DocumentTransformError):
    code = "incompatible_pending_output"
    public_detail = "An incomplete output from an older transform needs review"


class ModelUnavailableError(DocumentTransformError):
    code = "model_unavailable"
    public_detail = "The document transformation model is temporarily unavailable"


class DocumentGatewayError(DocumentTransformError):
    code = "document_provider_unavailable"
    public_detail = "Google Drive or Docs could not complete the transformation"

    def __init__(
        self,
        *,
        retryable: bool = True,
        http_status: int | None = None,
    ) -> None:
        super().__init__(self.public_detail)
        self.retryable = retryable
        self.http_status = http_status


class InPlaceNotAllowedError(DocumentTransformError):
    code = "in_place_not_allowed"
    public_detail = "This document is not eligible for safe in-place transformation"


class InPlaceNeedsReviewError(DocumentTransformError):
    code = "needs_review"
    public_detail = "The source needs review before it can be changed in place"


class InPlacePostMutationVerificationError(InPlaceNeedsReviewError):
    code = "in_place_verification_failed"
    public_detail = (
        "Google Docs accepted changes, but the updated document did not pass safety "
        "verification. The original document may have changed; inspect it and its Drive "
        "version history before trying again"
    )


class UnsupportedTabMetadataError(InPlaceNeedsReviewError):
    public_detail = "This document's tab metadata cannot be safely renamed"


class ActiveSuggestionsNeedsReviewError(InPlaceNeedsReviewError):
    public_detail = "Resolve active Google Docs suggestions before polishing in place"


class CustomHeaderNeedsReviewError(InPlaceNeedsReviewError):
    public_detail = "Custom headers or footers need review before Sarj branding can be applied"


class BrandAssetUnavailableError(DocumentTransformError):
    code = "brand_asset_unavailable"
    public_detail = "Sarj branding is temporarily unavailable; the document was not changed"


class CheckpointTooLargeError(DocumentTransformError):
    code = "checkpoint_too_large"
    public_detail = "The document transformation plan is too large to execute safely"
