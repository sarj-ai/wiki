import type { BlockStyle } from "@/types";

/**
 * Returns the URL if it uses a safe protocol, or null otherwise.
 * Prevents javascript: and data: protocol injection from untrusted content.
 */
export function sanitizeUrl(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url, window.location.origin);
    if (
      parsed.protocol === "http:" ||
      parsed.protocol === "https:" ||
      parsed.protocol === "mailto:"
    ) {
      return url;
    }
    return undefined;
  } catch {
    // Relative URLs (e.g. #heading-id) are safe
    if (url.startsWith("#") || url.startsWith("/")) {
      return url;
    }
    return undefined;
  }
}

export const STATUS_CLASSES: Record<string, string> = {
  "not started": "doc-status-neutral",
  "in progress": "doc-status-active",
  "in review": "doc-status-active",
  blocked: "doc-status-blocked",
  approved: "doc-status-success",
  launched: "doc-status-success",
  completed: "doc-status-success",
  done: "doc-status-success",
  "on hold": "doc-status-warning",
  paused: "doc-status-warning",
  cancelled: "doc-status-blocked",
  canceled: "doc-status-blocked",
  pending: "doc-status-warning",
  draft: "doc-status-neutral",
};

export function getStatusClass(text: string): string | null {
  return STATUS_CLASSES[text.trim().toLowerCase()] ?? null;
}

export const isValidCssColor = (v: string) =>
  /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$|^rgba?\([^)]+\)$/.test(v);
export const isValidCssFont = (v: string) => /^[a-zA-Z0-9\s,'"_-]+$/.test(v);
export const isFiniteNumber = (v: unknown): v is number => typeof v === "number" && isFinite(v);

const VALID_ALIGNMENTS = new Set(["center", "right", "justify"]);
const VALID_DIRECTIONS = new Set(["rtl"]);
const VALID_VERTICAL_ALIGNS = new Set(["top", "middle", "bottom", "baseline"]);

export function buildBlockCss(style?: BlockStyle): React.CSSProperties | undefined {
  if (!style) return undefined;
  const css: React.CSSProperties = {};
  if (style.alignment && VALID_ALIGNMENTS.has(style.alignment)) css.textAlign = style.alignment;
  if (style.direction && VALID_DIRECTIONS.has(style.direction)) css.direction = style.direction;
  if (style.backgroundColor && isValidCssColor(style.backgroundColor))
    css.backgroundColor = style.backgroundColor;
  if (isFiniteNumber(style.indentStart)) css.paddingInlineStart = `${style.indentStart}pt`;
  return Object.keys(css).length > 0 ? css : undefined;
}

export { VALID_VERTICAL_ALIGNS };

export function guessLanguage(code: string): string {
  const first = code.trimStart().slice(0, 200);
  if (/^(import |export |const |let |var |function |class |=>|async )/.test(first))
    return "typescript";
  if (/^(def |class |import |from |if __name__|print\()/.test(first)) return "python";
  if (
    /^(SELECT |INSERT |UPDATE |DELETE |CREATE |ALTER |DROP |"SELECT|"INSERT|"UPDATE|"DELETE)/i.test(
      first,
    )
  )
    return "sql";
  if (/^(package |func |import \(|type |var )/.test(first)) return "go";
  if (/^(<\?php|namespace |use |public function)/.test(first)) return "php";
  if (/^(#include|int main|void |printf\()/.test(first)) return "c";
  if (/^(\$ |# |sudo |npm |yarn |pip |curl |wget |cd |ls |mkdir |echo |cat )/.test(first))
    return "bash";
  if (/^\{[\s\n]*("|')/.test(first)) return "json";
  if (/^(<html|<div|<span|<!DOCTYPE)/i.test(first)) return "html";
  if (/^(apiVersion:|kind:|metadata:)/.test(first)) return "yaml";
  if (/^(FROM |RUN |CMD |COPY |EXPOSE |ENTRYPOINT )/i.test(first)) return "dockerfile";
  return "text";
}
