from __future__ import annotations


DOCUMENT_PIPELINE_VERSION = "document-transform-v4"
PRESENTATION_PIPELINE_VERSION = "presentation-transform-v2"
WORD_PIPELINE_VERSION = "word-format-copy-v1"

CURRENT_PIPELINE_VERSIONS = frozenset(
    {
        DOCUMENT_PIPELINE_VERSION,
        PRESENTATION_PIPELINE_VERSION,
        WORD_PIPELINE_VERSION,
    }
)
