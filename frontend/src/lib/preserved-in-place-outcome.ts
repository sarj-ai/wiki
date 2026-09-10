import type { DocumentTransformResult } from "@/lib/document-transform-types";

export function preservedInPlaceOutcome(result: DocumentTransformResult | null | undefined) {
  if (
    !result?.needsReview ||
    result.outputStrategy !== "in_place" ||
    result.artifactKind !== "document" ||
    result.mode === "unslop" ||
    !result.validation.protectedContentPreserved ||
    !result.validation.structurePreserved ||
    result.validation.unsafePatchCount !== 0 ||
    result.findings.length === 0 ||
    !result.findings.every(
      ({ code }) =>
        code === "native_rich_text_preserved" ||
        code === "native_font_family_preserved" ||
        code === "native_visual_authority_preserved" ||
        code === "custom_furniture_preserved" ||
        code === "native_paragraph_layout_preserved" ||
        code === "table_font_scope" ||
        code === "table_font_preserved",
    )
  ) {
    return null;
  }
  return {
    heading: result.changed
      ? "Polish complete with preserved content"
      : "No changes needed; review notes remain",
    message: result.changed
      ? "Updated the original document. Preservation checks passed; review the notes for content and formatting kept unchanged."
      : "The original document is unchanged. Preservation checks passed; review the notes for content and formatting kept unchanged.",
    openLabel: result.changed ? "Open updated document" : "Open original document",
  };
}
