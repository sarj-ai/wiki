"""Transform Google Docs API response into simplified content blocks.

Re-exports the public API so ``from app.docs_transformer import X`` still works.
"""

from app.docs_transformer.chips import enrich_chips_from_html
from app.docs_transformer.tabs import transform_doc_with_tabs
from app.docs_transformer.transform import transform_doc

__all__ = ["transform_doc_with_tabs", "transform_doc", "enrich_chips_from_html"]
