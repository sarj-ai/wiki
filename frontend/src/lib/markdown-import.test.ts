import {
  MARKDOWN_LIMIT_BYTES,
  markdownByteLength,
  MarkdownImportFileError,
  markdownTextError,
  readMarkdownFile,
} from "./markdown-import";

describe("Markdown import", () => {
  it("uses the UTF-8 byte boundary instead of JavaScript character length", () => {
    expect(markdownByteLength("é")).toBe(2);
    expect(markdownTextError("é".repeat(MARKDOWN_LIMIT_BYTES / 2))).toBeNull();
    expect(markdownTextError(`é${"a".repeat(MARKDOWN_LIMIT_BYTES - 1)}`)).toContain("512 KiB");
  });

  it("decodes a local .md file strictly and derives its document name", async () => {
    const imported = await readMarkdownFile(new File(["# Launch\n"], "Launch Plan.MD"));

    expect(imported).toEqual({ content: "# Launch\n", name: "Launch Plan" });
  });

  it("rejects non-Markdown names, invalid UTF-8, and oversized bytes", async () => {
    await expect(readMarkdownFile(new File(["text"], "notes.txt"))).rejects.toBeInstanceOf(
      MarkdownImportFileError,
    );
    await expect(
      readMarkdownFile(new File([new Uint8Array([0xc3, 0x28])], "invalid.md")),
    ).rejects.toThrow("not valid UTF-8");
    await expect(
      readMarkdownFile(new File([new Uint8Array(MARKDOWN_LIMIT_BYTES + 1)], "large.md")),
    ).rejects.toThrow("512 KiB");
  });
});
