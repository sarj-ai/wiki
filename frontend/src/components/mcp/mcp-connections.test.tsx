import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type * as ConsentClient from "@/lib/mcp-consent-client";
import {
  disconnectMcpConnection,
  getMcpConnections,
  type McpConnections as ConnectionData,
  McpConsentRequestError,
} from "@/lib/mcp-consent-client";

import { McpConnections } from "./mcp-connections";

vi.mock("@/lib/mcp-consent-client", async (importOriginal) => ({
  ...(await importOriginal<typeof ConsentClient>()),
  disconnectMcpConnection: vi.fn(),
  getMcpConnections: vi.fn(),
}));

const READ_ID = "019c6e27-e55b-73d1-87d8-4e01f1f75043";
const WRITE_ID = "019c7714-3b77-74d1-9866-e1f484aae2ab";
const INITIAL: ConnectionData = {
  csrf: "a".repeat(64),
  connections: [
    {
      grant_id: READ_ID,
      client_id: "read-client",
      capabilities: ["wiki:read"],
      expires_at: "2026-10-01T00:00:00Z",
    },
    {
      grant_id: WRITE_ID,
      client_id: "write-client",
      capabilities: ["wiki:read", "wiki:transform"],
      expires_at: "2026-10-01T00:00:00Z",
    },
  ],
};

function deferred<T>() {
  return Promise.withResolvers<T>();
}

describe("McpConnections", () => {
  let container: HTMLDivElement;
  let root: Root;
  const read = vi.mocked(getMcpConnections);
  const disconnect = vi.mocked(disconnectMcpConnection);

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    read.mockReset().mockResolvedValue(INITIAL);
    disconnect.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    container.remove();
  });

  function render() {
    flushSync(() => root.render(<McpConnections />));
  }

  function buttons(name: string): HTMLButtonElement[] {
    return [...container.querySelectorAll("button")].filter(
      (candidate) => candidate.textContent.trim() === name,
    );
  }

  function button(name: string): HTMLButtonElement {
    const found = buttons(name)[0];
    if (!found) throw new Error(`Missing button ${name}`);
    return found;
  }

  async function loaded() {
    await vi.waitFor(() => expect(container).toHaveTextContent("read-client"));
  }

  it("shows no setup command before a successful authenticated read", async () => {
    const pending = deferred<ConnectionData>();
    read.mockReturnValueOnce(pending.promise);
    render();
    expect(container.querySelector("code")).toBeNull();
    pending.resolve({ csrf: INITIAL.csrf, connections: [] });
    await vi.waitFor(() => expect(container).toHaveTextContent("No active app connections."));
    expect(container.querySelector("code")).toHaveTextContent("codex mcp add sarj-wiki");
    expect(container.querySelector("code")).toHaveTextContent("--url");
    expect(container.querySelector("code")).toHaveTextContent("codex mcp login sarj-wiki");
    expect(container.querySelector("code")).not.toHaveTextContent("--oauth-client-registration");
    expect(buttons("Disconnect")).toHaveLength(0);
  });

  it.each(["initial", "refresh"] as const)(
    "aborts the %s read on unmount and leaves its late result unpublished",
    async (kind) => {
      const pending = deferred<ConnectionData>();
      if (kind === "initial") read.mockReturnValueOnce(pending.promise);
      else read.mockResolvedValueOnce(INITIAL).mockReturnValueOnce(pending.promise);
      render();
      if (kind === "refresh") {
        await loaded();
        button("Refresh status").click();
      }
      const signal = read.mock.lastCall?.[0];
      expect(signal?.aborted).toBe(false);

      flushSync(() => root.render(null));

      expect(signal?.aborted).toBe(true);
      pending.resolve(INITIAL);
      await pending.promise;
      await Promise.resolve();
      expect(container).toBeEmptyDOMElement();
      expect(disconnect).not.toHaveBeenCalled();
    },
  );

  it("keeps an already-dispatched disconnect separate from read cleanup", async () => {
    const pending = deferred<undefined>();
    disconnect.mockReturnValueOnce(pending.promise);
    render();
    await loaded();
    button("Disconnect").click();
    const readSignal = read.mock.calls[0]?.[0];

    flushSync(() => root.render(null));

    expect(readSignal?.aborted).toBe(true);
    expect(disconnect).toHaveBeenCalledExactlyOnceWith(READ_ID, INITIAL.csrf);
    pending.resolve(undefined);
    await pending.promise;
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it("labels read-only versus edit grants and includes real expiry elements", async () => {
    render();
    await loaded();
    expect(container).toHaveTextContent("Read-only access");
    expect(container).toHaveTextContent("Read and edit access");
    expect(container.querySelectorAll("time")).toHaveLength(2);
    expect(container.querySelector("time")).toHaveAttribute(
      "dateTime",
      INITIAL.connections[0]?.expires_at,
    );
  });

  it("offers sign-in on 401 without exposing the setup command", async () => {
    read.mockRejectedValueOnce(new McpConsentRequestError(401));
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent("Sign in to Wiki"));
    expect(container.querySelector("code")).toBeNull();
    expect(container.querySelector("a")).toHaveAttribute(
      "href",
      "/login?redirect=%2Fmcp%2Fconnections",
    );
  });

  it("does not show setup or imply sign-in repairs a disabled integration", async () => {
    read.mockRejectedValueOnce(new McpConsentRequestError(503));
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent("connections are unavailable"));
    expect(container.querySelector("code")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
  });

  it("clears a stale sign-in prompt after authenticated recovery", async () => {
    read.mockRejectedValueOnce(new McpConsentRequestError(401));
    render();
    await vi.waitFor(() => expect(container).toHaveTextContent("Sign in to Wiki"));
    button("Refresh status").click();
    await loaded();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("code")).toHaveTextContent("codex mcp add sarj-wiki");
  });

  it("latches duplicate and competing disconnects before rerender", async () => {
    const pending = deferred<undefined>();
    disconnect.mockReturnValueOnce(pending.promise);
    render();
    await loaded();
    const actions = buttons("Disconnect");
    actions[0]?.click();
    actions[0]?.click();
    actions[1]?.click();
    button("Refresh status").click();
    expect(disconnect).toHaveBeenCalledExactlyOnceWith(READ_ID, INITIAL.csrf);
    expect(read).toHaveBeenCalledOnce();
    await vi.waitFor(() => expect(button("Refresh status")).toBeDisabled());
    pending.resolve(undefined);
    await vi.waitFor(() => expect(buttons("Disconnect")).toHaveLength(0));
  });

  it("keeps ambiguous deletion controls absent through failed recovery, then reads fresh state", async () => {
    disconnect.mockRejectedValueOnce(new TypeError("network"));
    render();
    await loaded();
    button("Disconnect").click();
    await vi.waitFor(() => expect(container).toHaveTextContent("result is uncertain"));
    expect(buttons("Disconnect")).toHaveLength(0);
    expect(container.querySelector("code")).toBeNull();
    read.mockRejectedValueOnce(new TypeError("network"));
    button("Refresh status").click();
    await vi.waitFor(() => expect(container).toHaveTextContent("Could not confirm"));
    expect(buttons("Disconnect")).toHaveLength(0);
    read.mockResolvedValueOnce({
      csrf: "b".repeat(64),
      connections: INITIAL.connections.filter((connection) => connection.grant_id === WRITE_ID),
    });
    button("Refresh status").click();
    await vi.waitFor(() => expect(buttons("Disconnect")).toHaveLength(1));
    expect(container).not.toHaveTextContent("read-client");
    expect(container).toHaveTextContent("write-client");
    button("Disconnect").click();
    await vi.waitFor(() => expect(disconnect).toHaveBeenLastCalledWith(WRITE_ID, "b".repeat(64)));
  });

  it("does not optimistically keep grants after successful deletion and requires readback", async () => {
    render();
    await loaded();
    button("Disconnect").click();
    await vi.waitFor(() =>
      expect(container).toHaveTextContent("Existing admitted document jobs may still finish"),
    );
    expect(buttons("Disconnect")).toHaveLength(0);
    read.mockResolvedValueOnce({ csrf: INITIAL.csrf, connections: [] });
    button("Refresh status").click();
    await vi.waitFor(() => expect(container).toHaveTextContent("No active app connections."));
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it("does not let a stale initial read undo a later uncertain disconnect", async () => {
    const initial = deferred<ConnectionData>();
    read.mockReturnValueOnce(initial.promise).mockResolvedValueOnce(INITIAL);
    disconnect.mockRejectedValueOnce(new TypeError("network"));
    render();
    expect(button("Refresh status")).toBeDisabled();
    button("Refresh status").click();
    expect(read).toHaveBeenCalledOnce();
    initial.resolve(INITIAL);
    await loaded();
    button("Disconnect").click();
    await vi.waitFor(() => expect(container).toHaveTextContent("result is uncertain"));
    initial.resolve(INITIAL);
    await vi.waitFor(() => expect(button("Refresh status")).toBeEnabled());
    await Promise.resolve();
    expect(buttons("Disconnect")).toHaveLength(0);
    expect(container.querySelector("code")).toBeNull();
  });
});
