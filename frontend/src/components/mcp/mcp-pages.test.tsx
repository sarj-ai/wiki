import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import ConnectionsPage, { metadata as connectionsMetadata } from "@/app/mcp/connections/page";
import ConsentPage, { metadata as consentMetadata } from "@/app/mcp/consent/page";
import * as transport from "@/lib/mcp-consent-client";

const INTENT = `wm_intent_${"a".repeat(43)}`;

describe("MCP page navigation", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    vi.spyOn(transport, "getMcpConnections").mockResolvedValue({
      connections: [],
      csrf: "a".repeat(64),
    });
    vi.spyOn(transport, "getMcpConsent").mockResolvedValue({
      account_email: "person@example.com",
      client_id: "app",
      client_name: "Requested app",
      csrf: `wm_intent_${"b".repeat(43)}`,
      scopes: ["wiki:read", "wiki:transform"],
      status: "pending",
    });
    vi.spyOn(transport, "decideMcpConsent");
    vi.spyOn(transport, "disconnectMcpConnection");
    vi.spyOn(transport, "recoverMcpConsent");
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("lets the parent title template supply branding exactly once", () => {
    expect(connectionsMetadata.title).toBe("App connections");
    expect(consentMetadata.title).toBe("Review app access");
  });

  it.each([
    ["connections", "loading"],
    ["connections", "error"],
    ["connections", "ready"],
    ["consent", "loading"],
    ["consent", "error"],
    ["consent", "ready"],
  ] as const)("keeps a non-mutating return link on %s %s", async (page, state) => {
    if (state === "loading") {
      vi.mocked(transport.getMcpConnections).mockReturnValue(
        Promise.withResolvers<never>().promise,
      );
      vi.mocked(transport.getMcpConsent).mockReturnValue(Promise.withResolvers<never>().promise);
    } else if (state === "error") {
      vi.mocked(transport.getMcpConnections).mockRejectedValue(
        new transport.McpConsentRequestError(404),
      );
      vi.mocked(transport.getMcpConsent).mockRejectedValue(
        new transport.McpConsentRequestError(404),
      );
    }
    const content =
      page === "connections" ? (
        <ConnectionsPage />
      ) : (
        await ConsentPage({ searchParams: Promise.resolve({ intent: INTENT }) })
      );
    flushSync(() => root.render(content));
    const contentByState = {
      error: /unavailable/i,
      loading: /Checking/,
      ready: page === "connections" ? "No active app connections." : "Allow read access",
    };
    await vi.waitFor(() => expect(container).toHaveTextContent(contentByState[state]));
    const link = container.querySelector<HTMLAnchorElement>('a[href="/"]');
    expect(link).toHaveTextContent("Back to Wiki");
    expect(link).not.toHaveAttribute("aria-disabled");
    expect(link?.closest("form")).toBeNull();
    const reads = [
      vi.mocked(transport.getMcpConnections).mock.calls.length,
      vi.mocked(transport.getMcpConsent).mock.calls.length,
    ];
    link?.addEventListener("click", (event) => event.preventDefault(), { capture: true });
    link?.click();
    expect(transport.decideMcpConsent).not.toHaveBeenCalled();
    expect(transport.disconnectMcpConnection).not.toHaveBeenCalled();
    expect(transport.recoverMcpConsent).not.toHaveBeenCalled();
    expect([
      vi.mocked(transport.getMcpConnections).mock.calls.length,
      vi.mocked(transport.getMcpConsent).mock.calls.length,
    ]).toEqual(reads);
  });

  it("keeps the return link on an invalid consent request", async () => {
    const content = await ConsentPage({ searchParams: Promise.resolve({ intent: "invalid" }) });
    flushSync(() => root.render(content));
    expect(container.querySelector('a[href="/"]')).toHaveTextContent("Back to Wiki");
    expect(container.querySelector('[role="alert"]')).toHaveTextContent(
      "Invalid connection request",
    );
    expect(transport.getMcpConsent).not.toHaveBeenCalled();
    expect(transport.decideMcpConsent).not.toHaveBeenCalled();
  });
});
