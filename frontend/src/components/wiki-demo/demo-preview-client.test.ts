import { DEMO_PREVIEW_CLIENT } from "./demo-preview-client";

const HTTP = vi.fn<typeof fetch>();
const HTML = "<!doctype html><html><body>Public sample</body></html>";

describe("public demo preview transport", () => {
  beforeEach(() => {
    HTTP.mockReset();
    vi.stubGlobal("fetch", HTTP);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("makes exactly one credential-free same-origin request with caller cancellation", async () => {
    HTTP.mockResolvedValueOnce(
      new Response(HTML, { headers: { "content-type": "text/html; charset=utf-8" } }),
    );
    const controller = new AbortController();
    await expect(DEMO_PREVIEW_CLIENT.load("budget", controller.signal)).resolves.toBe(HTML);
    expect(HTTP).toHaveBeenCalledExactlyOnceWith("/demo/files/northstar-budget.html", {
      credentials: "omit",
      headers: { Accept: "text/html" },
      redirect: "error",
      signal: controller.signal,
    });
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
    await expect(
      DEMO_PREVIEW_CLIENT.load("launch-brief-clean", new AbortController().signal),
    ).rejects.toThrow(message);
  });

  it("propagates network rejection to the owning viewer", async () => {
    HTTP.mockRejectedValueOnce(new TypeError("Network failed"));
    await expect(DEMO_PREVIEW_CLIENT.load("status", new AbortController().signal)).rejects.toThrow(
      "Network failed",
    );
  });
});
