import { webcrypto } from "node:crypto";

import type * as DemoDataModule from "./demo-data";
import { DEMO_PREVIEW_CLIENT } from "./demo-preview-client";

const LOCAL_FILES = vi.hoisted(() => {
  const base = {
    description: "Public transport fixture",
    downloadFilename: "public-document.docx",
    folder: "documents" as const,
    format: "Word document",
    keywords: "public",
    mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    name: "Public document.docx",
  };
  return [
    { ...base, id: "static", previewFilename: "public-document.html" },
    { ...base, id: "envelope", previewFilename: "public-document-preview.json" },
    {
      ...base,
      id: "native",
      previewUrl:
        "https://docs.google.com/document/d/11ZKT_SKqzN17KzjRQ_iyoqgM6HjYEdFvokNOQFpExlk/preview",
    },
    { ...base, id: "missing" },
  ];
});

vi.mock("./demo-data", async () => {
  const actual = await vi.importActual<typeof DemoDataModule>("./demo-data");
  return { ...actual, DEMO_FILES: LOCAL_FILES };
});

const HTTP = vi.fn<typeof fetch>();
const HTML = "<!doctype html><html><body>Public sample</body></html>";
const HTML_SHA256 = "1b47b4fc9abe3730ae8b985a77767ae39f684f593bb8a8b64920c48b78b06695";

describe("public demo preview transport", () => {
  beforeEach(() => {
    HTTP.mockReset();
    vi.stubGlobal("fetch", HTTP);
    vi.stubGlobal("crypto", webcrypto);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("makes exactly one credential-free same-origin request with caller cancellation", async () => {
    HTTP.mockResolvedValueOnce(
      new Response(HTML, { headers: { "content-type": "text/html; charset=utf-8" } }),
    );
    const controller = new AbortController();
    await expect(DEMO_PREVIEW_CLIENT.load("static", controller.signal)).resolves.toBe(HTML);
    expect(HTTP).toHaveBeenCalledExactlyOnceWith("/demo/files/public-document.html", {
      credentials: "omit",
      headers: { Accept: "text/html" },
      redirect: "error",
      signal: controller.signal,
    });
  });

  it("renders only the verified HTML from a credential-free JSON envelope", async () => {
    HTTP.mockResolvedValueOnce(Response.json({ html: HTML, sha256: HTML_SHA256 }));
    const controller = new AbortController();
    await expect(DEMO_PREVIEW_CLIENT.load("envelope", controller.signal)).resolves.toBe(HTML);
    expect(HTTP).toHaveBeenCalledExactlyOnceWith("/demo/files/public-document-preview.json", {
      credentials: "omit",
      headers: { Accept: "application/json" },
      redirect: "error",
      signal: controller.signal,
    });
  });

  it.each(["text/html", "text/plain", "application/problem+json"])(
    "rejects an envelope served with the wrong MIME type: %s",
    async (type) => {
      HTTP.mockResolvedValueOnce(
        Response.json(
          { html: HTML, sha256: HTML_SHA256 },
          {
            headers: { "content-type": type },
          },
        ),
      );
      await expect(
        DEMO_PREVIEW_CLIENT.load("envelope", new AbortController().signal),
      ).rejects.toThrow("The sample preview is unavailable");
    },
  );

  it.each([
    null,
    [],
    {},
    { html: HTML },
    { html: 42, sha256: HTML_SHA256 },
    { html: " ".repeat(3), sha256: HTML_SHA256 },
    { html: HTML, sha256: "" },
    { html: HTML, sha256: "not-a-checksum" },
    { html: HTML, sha256: HTML_SHA256, extra: true },
    { html: `${HTML}<script>unwanted()</script>`, sha256: HTML_SHA256 },
  ])("rejects a malformed or altered envelope: %j", async (envelope) => {
    HTTP.mockResolvedValueOnce(Response.json(envelope));
    await expect(
      DEMO_PREVIEW_CLIENT.load("envelope", new AbortController().signal),
    ).rejects.toThrow("The sample preview is invalid");
  });

  it("rejects injected non-JSON text before an iframe can render", async () => {
    HTTP.mockResolvedValueOnce(
      new Response('{"html":"public"}<script>unwanted()</script>', {
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(
      DEMO_PREVIEW_CLIENT.load("envelope", new AbortController().signal),
    ).rejects.toThrow("The sample preview is invalid");
  });

  it("preserves caller cancellation while the envelope is being read", async () => {
    const controller = new AbortController();
    const response = Response.json({ html: HTML, sha256: HTML_SHA256 });
    const read = Promise.withResolvers<unknown>();
    const reading = Promise.withResolvers<undefined>();
    vi.spyOn(response, "json").mockImplementationOnce(async () => {
      reading.resolve(undefined);
      return read.promise;
    });
    HTTP.mockResolvedValueOnce(response);
    const pending = DEMO_PREVIEW_CLIENT.load("envelope", controller.signal);
    await reading.promise;
    controller.abort();
    read.resolve({ html: HTML, sha256: HTML_SHA256 });
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it.each(["private-id", "https://example.invalid/private", "../api/wiki/drives"])(
    "rejects an unknown source before any request: %s",
    async (id) => {
      await expect(DEMO_PREVIEW_CLIENT.load(id, new AbortController().signal)).rejects.toThrow(
        "Unknown demo sample",
      );
      expect(HTTP).not.toHaveBeenCalled();
    },
  );

  it.each(["native", "missing"])(
    "never substitutes a local HTML request for a native or missing preview: %s",
    async (id) => {
      await expect(DEMO_PREVIEW_CLIENT.load(id, new AbortController().signal)).rejects.toThrow(
        "The sample does not use a local HTML preview",
      );
      expect(HTTP).not.toHaveBeenCalled();
    },
  );

  it.each([
    {
      name: "HTTP 404",
      body: "Missing",
      status: 404,
      type: "text/html",
      message: "The sample preview is unavailable",
    },
    {
      name: "unexpected JSON",
      body: "{}",
      status: 200,
      type: "application/json",
      message: "The sample preview is unavailable",
    },
    {
      name: "empty HTML",
      body: "  ",
      status: 200,
      type: "text/html",
      message: "The sample preview is empty",
    },
  ])("rejects $name before a frame can render", async ({ body, message, status, type }) => {
    HTTP.mockResolvedValueOnce(new Response(body, { status, headers: { "content-type": type } }));
    await expect(DEMO_PREVIEW_CLIENT.load("static", new AbortController().signal)).rejects.toThrow(
      message,
    );
  });

  it("propagates network rejection to the owning viewer", async () => {
    HTTP.mockRejectedValueOnce(new TypeError("Network failed"));
    await expect(DEMO_PREVIEW_CLIENT.load("static", new AbortController().signal)).rejects.toThrow(
      "Network failed",
    );
  });
});
