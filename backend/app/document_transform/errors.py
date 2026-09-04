class DocumentTransformError(Exception):
    code = "document_transform_failed"
    public_detail = "The document could not be transformed safely"


class SourceChangedError(DocumentTransformError):
    code = "source_changed"
    public_detail = "The source changed after the transformation was requested"


class SourceTooLargeError(DocumentTransformError):
    code = "source_too_large"
    public_detail = "The source exceeds the document transformation limit"


class UnsupportedSourceError(DocumentTransformError):
    code = "unsupported_source"
    public_detail = "This source cannot be transformed without losing content"


class OutputConflictError(DocumentTransformError):
    code = "output_conflict"
    public_detail = "The existing output belongs to a different source revision"


class IncompatiblePendingOutputError(DocumentTransformError):
    code = "incompatible_pending_output"
    public_detail = "An incomplete output from an older transform needs review"


class ModelUnavailableError(DocumentTransformError):
    code = "model_unavailable"
    public_detail = "The document transformation model is temporarily unavailable"


class DocumentGatewayError(DocumentTransformError):
    code = "document_provider_unavailable"
    public_detail = "Google Drive or Docs could not complete the transformation"


class HeaderConflictError(DocumentTransformError):
    code = "header_conflict"
    public_detail = "The source header needs review before Sarj branding can be applied"
