import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import * as transport from "@/lib/mcp-consent-client";

import { McpConsentLoader } from "./mcp-consent-loader";

const INTENT = `wm_intent_${"a".repeat(43)}`;
const VIEW: transport.McpConsentView = {
  account_email: "person@example.com",
  client_id: "app",
  client_name: "Requested app",
  csrf: `wm_intent_${"b".repeat(43)}`,
  scopes: ["wiki:read", "wiki:transform"],
  status: "pending",
};

describe("McpConsentLoader", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    vi.spyOn(transport, "getMcpConsent").mockResolvedValue(VIEW);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  function render() {
    flushSync(() => root.render(<McpConsentLoader intent={INTENT} />));
  }

  it("loads the authenticated account and defaults to reading", async () => {
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent("person@example.com"));
    expect(container).toHaveTextContent("Requested app");
    expect(container.querySelector('[role="checkbox"]')).not.toBeChecked();
    expect(container).toHaveTextContent("Allow read access");
  });

  it.each(["approved", "denied"] as const)(
    "keeps a reloaded %s request terminal",
    async (status) => {
      vi.mocked(transport.getMcpConsent).mockResolvedValue({ ...VIEW, status });
      render();
      await vi.waitFor(() => expect(container).toHaveTextContent("Requested app"));
      for (const button of container.querySelectorAll("button")) expect(button).toBeDisabled();
      expect(container).toHaveTextContent(
        status === "approved" ? "Start a new connection" : "Access denied",
      );
    },
  );

  it("offers a same-app login continuation on 401 without granting access", async () => {
    vi.mocked(transport.getMcpConsent).mockRejectedValue(new transport.McpConsentRequestError(401));
    const decide = vi.spyOn(transport, "decideMcpConsent");
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent("Sign in to review access"));
    const href = container.querySelector("a")?.getAttribute("href");
    expect(href).toBeDefined();
    const login = new URL(href ?? "", "https://wiki.example");
    expect(login.pathname).toBe("/login");
    expect(login.searchParams.get("redirect")).toBe(`/mcp/consent?intent=${INTENT}`);
    expect(decide).not.toHaveBeenCalled();
  });

  it("shows unavailable without approval controls when the endpoint is disabled", async () => {
    vi.mocked(transport.getMcpConsent).mockRejectedValue(new transport.McpConsentRequestError(404));
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent("Connection request unavailable"));
    expect(container.querySelector("button")).toBeNull();
  });
});
