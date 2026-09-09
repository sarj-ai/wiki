export const MARKDOWN_LIMIT_BYTES = 512 * 1024;

export class MarkdownImportFileError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "MarkdownImportFileError";
  }
}

export interface MarkdownFileContents {
  content: string;
  name: string;
}

const ENCODER = new TextEncoder();

export function markdownByteLength(value: string): number {
  return ENCODER.encode(value).byteLength;
}

export function markdownTextError(value: string): null | string {
  if (
    new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(ENCODER.encode(value)) !==
    value
  ) {
    return "Markdown must contain valid UTF-8 text.";
  }
  if (markdownByteLength(value) > MARKDOWN_LIMIT_BYTES) {
    return "Markdown must be 512 KiB or smaller when encoded as UTF-8.";
  }
  return null;
}

export async function readMarkdownFile(file: File): Promise<MarkdownFileContents> {
  if (!file.name.toLocaleLowerCase("en-US").endsWith(".md")) {
    throw new MarkdownImportFileError("Choose a Markdown file whose name ends in .md.");
  }
  if (file.size > MARKDOWN_LIMIT_BYTES) {
    throw new MarkdownImportFileError(
      "Markdown files must be 512 KiB or smaller when encoded as UTF-8.",
    );
  }
  const bytes = await file.arrayBuffer();
  if (bytes.byteLength > MARKDOWN_LIMIT_BYTES) {
    throw new MarkdownImportFileError(
      "Markdown files must be 512 KiB or smaller when encoded as UTF-8.",
    );
  }
  let content: string;
  try {
    content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error: unknown) {
    throw new MarkdownImportFileError("The selected file is not valid UTF-8 text.", {
      cause: error,
    });
  }
  const name = file.name.slice(0, -3).trim().slice(0, 255) || "Untitled document";
  return { content, name };
}
