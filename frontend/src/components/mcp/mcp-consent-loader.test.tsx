import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import * as transport from "@/lib/mcp-consent-client";

import { McpConsentLoader } from "./mcp-consent-loader";

const INTENT = `wm_intent_${"a".repeat(43)}`;
const FIELDS = { clientName: "client_name", restartRequired: "restart_required" } as const;
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

  it("aborts the initial read on unmount and ignores its late result", async () => {
    const pending = Promise.withResolvers<transport.McpConsentView>();
    vi.mocked(transport.getMcpConsent).mockReturnValue(pending.promise);
    render();
    const signal = vi.mocked(transport.getMcpConsent).mock.calls[0]?.[1];
    expect(signal?.aborted).toBe(false);

    flushSync(() => root.render(null));

    expect(signal?.aborted).toBe(true);
    pending.resolve(VIEW);
    await pending.promise;
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });

  it("hides a previous ready view while a replacement intent's fresh read is pending", async () => {
    const nextIntent = `wm_intent_${"c".repeat(43)}`;
    const pending = Promise.withResolvers<transport.McpConsentView>();
    const read = vi.mocked(transport.getMcpConsent);
    const decide = vi.spyOn(transport, "decideMcpConsent").mockRejectedValue(new Error("network"));
    read.mockResolvedValueOnce(VIEW).mockReturnValueOnce(pending.promise);
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent(VIEW[FIELDS.clientName]));
    const originalSignal = read.mock.calls[0]?.[1];

    flushSync(() => root.render(<McpConsentLoader intent={nextIntent} />));

    expect(originalSignal?.aborted).toBe(true);
    expect(container).toHaveTextContent("Checking this connection request");
    expect(container).not.toHaveTextContent(VIEW[FIELDS.clientName]);
    expect(container.querySelector("button")).toBeNull();
    const nextView = { ...VIEW, [FIELDS.clientName]: "Replacement app", csrf: nextIntent };
    pending.resolve(nextView);
    await vi.waitFor(() => expect(container).toHaveTextContent(nextView[FIELDS.clientName]));
    const approve = [...container.querySelectorAll("button")].find(
      (button) => button.textContent.trim() === "Allow read access",
    );
    approve?.click();
    expect(decide).toHaveBeenCalledExactlyOnceWith(nextIntent, nextView.csrf, {
      approve: true,
      transformApproved: false,
    });
  });

  it("aborts pending decision-status recovery on unmount without resubmitting a decision", async () => {
    const pending =
      Promise.withResolvers<Awaited<ReturnType<typeof transport.recoverMcpConsent>>>();
    const decide = vi.spyOn(transport, "decideMcpConsent").mockRejectedValue(new Error("network"));
    const recover = vi.spyOn(transport, "recoverMcpConsent").mockReturnValue(pending.promise);
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent(VIEW[FIELDS.clientName]));
    const approve = [...container.querySelectorAll("button")].find(
      (button) => button.textContent.trim() === "Allow read access",
    );
    approve?.click();
    await vi.waitFor(() => expect(container.querySelector('[role="alert"]')).toBeInTheDocument());
    const check = [...container.querySelectorAll("button")].find(
      (button) => button.textContent.trim() === "Check status",
    );
    check?.click();
    const signal = recover.mock.calls[0]?.[1];
    expect(signal?.aborted).toBe(false);
    expect(recover).toHaveBeenCalledOnce();

    flushSync(() => root.render(null));

    expect(signal?.aborted).toBe(true);
    pending.resolve({ [FIELDS.restartRequired]: true, status: "approved" });
    await pending.promise;
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
    expect(decide).toHaveBeenCalledOnce();
  });

  it("requires a fresh owner when returning to an intent whose previous read was retired", async () => {
    const nextIntent = `wm_intent_${"c".repeat(43)}`;
    const otherRead = Promise.withResolvers<transport.McpConsentView>();
    const returnedRead = Promise.withResolvers<transport.McpConsentView>();
    const read = vi.mocked(transport.getMcpConsent);
    read
      .mockResolvedValueOnce(VIEW)
      .mockReturnValueOnce(otherRead.promise)
      .mockReturnValueOnce(returnedRead.promise);
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent(VIEW[FIELDS.clientName]));
    const previousOwner = read.mock.calls[0]?.[1];
    flushSync(() => root.render(<McpConsentLoader intent={nextIntent} />));
    const otherOwner = read.mock.calls[1]?.[1];

    render();

    expect(previousOwner?.aborted).toBe(true);
    expect(otherOwner?.aborted).toBe(true);
    expect(container).not.toHaveTextContent(VIEW[FIELDS.clientName]);
    expect(container.querySelector("button")).toBeNull();
    otherRead.resolve({ ...VIEW, [FIELDS.clientName]: "Discarded app" });
    await otherRead.promise;
    await Promise.resolve();
    expect(container).not.toHaveTextContent("Discarded app");
    returnedRead.resolve({ ...VIEW, [FIELDS.clientName]: "Fresh returned app" });
    await vi.waitFor(() => expect(container).toHaveTextContent("Fresh returned app"));
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
