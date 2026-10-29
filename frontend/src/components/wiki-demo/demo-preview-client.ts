import { DEMO_FILES, demoAssetUrl } from "./demo-data";

export const DEMO_PREVIEW_CLIENT = {
  async load(fileId: string, signal: AbortSignal): Promise<string> {
    const file = DEMO_FILES.find((candidate) => candidate.id === fileId);
    if (!file) throw new Error("Unknown demo sample");
    const response = await fetch(demoAssetUrl(file.previewFilename), {
      credentials: "omit",
      headers: { Accept: "text/html" },
      redirect: "error",
      signal,
    });
    const mimeType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
    if (!response.ok || mimeType !== "text/html") {
      throw new Error("The sample preview is unavailable");
    }
    const html = await response.text();
    if (!html.trim()) throw new Error("The sample preview is empty");
    return html;
  },
} as const;
