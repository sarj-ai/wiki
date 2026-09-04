MAX_MARKDOWN_BYTES = 512 * 1024

# WordprocessingML expands compact Markdown tokens into comparatively large XML
# elements.  Keep these limits independent of the source byte limit so a tiny
# adversarial table or inline-token stream cannot exhaust a worker while the
# DOCX package is being constructed.
MAX_DOCX_TABLE_ROWS = 500
MAX_DOCX_TABLE_COLUMNS = 32
MAX_DOCX_TABLE_CELLS = 4_096
MAX_DOCX_INLINE_RUNS = 4_096
MAX_DOCX_STYLED_SPANS = 3_072
MAX_DOCX_HYPERLINKS = 512
MAX_DOCX_ESTIMATED_XML_BYTES = 6 * 1024 * 1024
