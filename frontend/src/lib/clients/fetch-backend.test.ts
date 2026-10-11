// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchBackend } from "@/lib/clients/fetch-backend";

afterEach(() => vi.unstubAllGlobals());

describe("Cloudflare backend fetch", () => {
  it.each([200, 401, 403, 429, 503])(
    "preserves upstream status %s and streaming",
    async (status) => {
      const response = new Response("body", { status });
      const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response);
      vi.stubGlobal("fetch", fetchMock);
      const controller = new AbortController();
      const result = await fetchBackend("https://api-wiki.sarj.ai/sidebar/drives", {
        cache: "no-store",
        headers: { authorization: "Bearer test-token" },
        signal: controller.signal,
      });
      expect(result).toBe(response);
      expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
        "https://api-wiki.sarj.ai/sidebar/drives",
        expect.objectContaining({
          cache: "no-store",
          headers: { authorization: "Bearer test-token" },
          redirect: "manual",
          signal: controller.signal,
        }),
      );
    },
  );

  it.each([301, 302, 303, 307, 308])(
    "rejects redirect %s without following or exposing it",
    async (status) => {
      const cleanup = Promise.withResolvers<undefined>();
      const cancel = vi.fn<() => Promise<undefined>>().mockReturnValue(cleanup.promise);
      const response = new Response(new ReadableStream<Uint8Array>({ cancel }), {
        headers: { location: "https://other-origin.test/private-token" },
        status,
      });
      const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response);
      vi.stubGlobal("fetch", fetchMock);
      try {
        const result = await fetchBackend(
          new Request("https://api-wiki.sarj.ai/aliases/recent", {
            body: "{}",
            method: "POST",
          }),
        );
        expect(result.status).toBe(502);
        expect(result.headers.get("location")).toBeNull();
        await expect(result.text()).resolves.toBe("");
        expect(fetchMock).toHaveBeenCalledOnce();
        expect(cancel).toHaveBeenCalledOnce();
      } finally {
        cleanup.resolve(undefined);
      }
    },
  );
});
