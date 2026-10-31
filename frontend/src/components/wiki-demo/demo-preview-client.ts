import * as z from "zod";

import { DEMO_FILES, demoAssetUrl } from "./demo-data";

const PreviewEnvelopeSchema = z.strictObject({
  html: z.string().regex(/\S/u),
  sha256: z.string().regex(/^[a-f0-9]{64}$/u),
});

export const DEMO_PREVIEW_CLIENT = {
  async load(fileId: string, signal: AbortSignal): Promise<string> {
    signal.throwIfAborted();
    const file = DEMO_FILES.find((candidate) => candidate.id === fileId);
    if (!file) throw new Error("Unknown demo sample");
    if (file.previewUrl !== undefined || !file.previewFilename) {
      throw new Error("The sample does not use a local HTML preview");
    }
    const envelope = file.previewFilename.endsWith(".json");
    const expectedMime = envelope ? "application/json" : "text/html";
    const response = await fetch(demoAssetUrl(file.previewFilename), {
      credentials: "omit",
      headers: { Accept: expectedMime },
      redirect: "error",
      signal,
    });
    const mimeType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
    if (!response.ok || mimeType !== expectedMime) {
      throw new Error("The sample preview is unavailable");
    }
    const html = envelope ? await envelopeHtml(response, signal) : await response.text();
    signal.throwIfAborted();
    if (!html.trim()) throw new Error("The sample preview is empty");
    return html;
  },
} as const;

async function envelopeHtml(response: Response, signal: AbortSignal): Promise<string> {
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    signal.throwIfAborted();
    throw new Error("The sample preview is invalid", { cause: error });
  }
  signal.throwIfAborted();
  const envelope = PreviewEnvelopeSchema.safeParse(value);
  if (!envelope.success) throw new Error("The sample preview is invalid");
  const { html, sha256 } = envelope.data;
  const digest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(html)),
  );
  signal.throwIfAborted();
  const actual = Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
  if (actual !== sha256) throw new Error("The sample preview is invalid");
  return html;
}
