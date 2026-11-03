import { DEMO_FILES, type DemoFile, demoFileFor, demoHref } from "./demo-data";
import {
  demoDownloadUrl,
  demoHrefForDocumentPath,
  demoPreviewUrl,
  demoViewUrl,
} from "./demo-file-urls";

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
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=552737644",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=0",
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
    "https://docs.google.com/document/d/public-doc/preview#gid=552737644",
    "https://docs.google.com/presentation/d/public-slides/preview#gid=552737644",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview?gid=552737644",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=-1",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=1.5",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=99999999999",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=%35",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=1&edit=true",
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

describe("public sample document links", () => {
  it.each(DEMO_FILES)("opens the known public $id in the anonymous demo", (file) => {
    const preview = demoPreviewUrl(file);
    if (preview === null) throw new Error("A public demo file needs a verified preview URL");
    const id = new URL(preview).pathname.split("/", 4)[3];
    if (id === undefined) throw new Error("A public demo preview must identify its file");
    expect(demoHrefForDocumentPath(`/d/${id}`)).toBe(demoHref(file));
  });

  it.each([
    "/d/private-document",
    "/d/public-document/edit",
    "/d/%70ublic-document",
    "/go/public-document",
    "/api/wiki/files/public-document",
  ])("keeps unknown and non-document paths private: %s", (path) => {
    expect(demoHrefForDocumentPath(path)).toBeNull();
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

  it("omits a download until a verified external URL is configured", () => {
    expect(demoDownloadUrl(sample({ downloadUrl: "" }))).toBeNull();
  });
});

describe("public Drive link boundary", () => {
  it.each([
    "https://docs.google.com/document/d/public-doc/preview",
    "https://docs.google.com/spreadsheets/d/public-sheet/preview#gid=0",
    "https://docs.google.com/presentation/d/public-slides/preview",
    "https://drive.google.com/file/d/public-office-file/view",
    "https://drive.google.com/file/d/public-office-file/preview",
  ])("accepts a read-only Google file link: %s", (viewUrl) => {
    expect(demoViewUrl(sample({ viewUrl }))).toBe(viewUrl);
  });

  it.each([
    "",
    "https://docs.google.com/document/d/public-doc/edit",
    "https://drive.google.com/file/d/public-doc/view?redirect=https://evil.example",
    "https://drive.google.com.evil.example/file/d/public-doc/view",
    "https://drive.google.com@evil.example/file/d/public-doc/view",
    "https://evil.example/public.docx",
    "/api/wiki/files/private-id",
    "javascript:alert(1)",
  ])("omits editor, private and untrusted links: %s", (viewUrl) => {
    expect(demoViewUrl(sample({ viewUrl }))).toBeNull();
  });
});
