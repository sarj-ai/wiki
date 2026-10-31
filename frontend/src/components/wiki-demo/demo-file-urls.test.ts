import { type DemoFile, demoFileFor } from "./demo-data";
import { demoDownloadUrl, demoPreviewUrl } from "./demo-file-urls";

function sample(overrides: Partial<DemoFile>): DemoFile {
  return { ...demoFileFor(null), ...overrides };
}

function insecurePreviewUrl(): string {
  const url = new URL("https://docs.google.com/document/d/public-doc/preview");
  url.protocol = "http:";
  return url.href;
}

describe("public Google preview URL boundary", () => {
  it.each([
    "https://docs.google.com/document/d/public-doc/preview",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview",
    "https://docs.google.com/presentation/d/public-slides/preview",
    "https://drive.google.com/file/d/public-office-file/preview",
  ])("accepts the native read-only route %s", (previewUrl) => {
    expect(demoPreviewUrl(sample({ previewUrl }))).toBe(previewUrl);
  });

  it.each([
    "https://docs.google.com/document/d/public-doc/edit",
    "https://docs.google.com/document/d/public-doc/view",
    "https://docs.google.com/document/d/public-doc/preview?edit=true",
    "https://docs.google.com/document/d/public-doc/preview#edit",
    "https://docs.google.com.evil.example/document/d/public-doc/preview",
    "https://docs.google.com@evil.example/document/d/public-doc/preview",
    insecurePreviewUrl(),
    "https://docs.google.com:8443/document/d/public-doc/preview",
    "https://drive.google.com/file/d/public-doc/edit",
    "https://docs.google.com/forms/d/public-form/preview",
    "https://docs.google.com/document/d/%2Fprivate/preview",
    "/api/wiki/files/private-id",
    "javascript:alert(1)",
  ])("rejects an editor or untrusted source: %s", (previewUrl) => {
    expect(demoPreviewUrl(sample({ previewUrl }))).toBeNull();
  });
});

describe("public sample download URL boundary", () => {
  it.each([
    "https://docs.google.com/document/d/public-doc/export?format=docx",
    "https://docs.google.com/spreadsheets/d/public-sheet/export?format=xlsx",
    "https://docs.google.com/presentation/d/public-slides/export/pptx",
    "https://drive.google.com/uc?export=download&id=public-office-file",
    "https://drive.google.com/uc?id=public-office-file&export=download",
  ])("accepts the explicit Google file export %s", (downloadUrl) => {
    expect(demoDownloadUrl(sample({ downloadUrl }))).toBe(downloadUrl);
  });

  it.each([
    "https://docs.google.com/document/d/public-doc/edit",
    "https://docs.google.com/document/d/public-doc/export?format=docx&redirect=https://evil.example",
    "https://evil.example/public.docx",
    "https://drive.google.com/uc?export=download&id=../private",
    "/api/wiki/files/private-id",
    "javascript:alert(1)",
  ])("does not substitute an untrusted export or private route: %s", (downloadUrl) => {
    expect(demoDownloadUrl(sample({ downloadUrl }))).toBeNull();
  });

  it("keeps the credential-free local asset path when no external export is configured", () => {
    const file = sample({ downloadFilename: "public-sample.xlsx" });
    delete file.downloadUrl;
    expect(demoDownloadUrl(file)).toBe("/demo/files/public-sample.xlsx");
  });
});
