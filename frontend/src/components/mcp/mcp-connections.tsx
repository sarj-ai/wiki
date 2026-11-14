"use client";

import Link from "next/link";
import {
  type Dispatch,
  type RefObject,
  type SetStateAction,
  useEffect,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import { API_URL } from "@/lib/env";
import {
  disconnectMcpConnection,
  getMcpConnections,
  type McpConnections as Connections,
  McpConsentRequestError,
} from "@/lib/mcp-consent-client";

interface ReadyConnections {
  kind: "ready";
  data: Connections;
  receipt: "loaded" | "refreshed";
}

type SettledConnections =
  | ReadyConnections
  | { kind: "signin" }
  | { kind: "unavailable" }
  | { kind: "disconnected" }
  | { kind: "disconnect-uncertain" }
  | { kind: "read-uncertain" };

type ConnectionsState =
  | SettledConnections
  | { kind: "loading" }
  | { kind: "refreshing"; previous: SettledConnections }
  | { kind: "disconnecting"; data: Connections; grantId: string };

const COPY_LABELS = {
  ready: "Copy setup command",
  copied: "Setup command copied",
  unavailable: "Copy unavailable; select the command above",
} as const;

export function McpConnections() {
  const [state, setState] = useState<ConnectionsState>({ kind: "loading" });
  const phase = useRef<ConnectionsState>(state);
  const reads = useRef<AbortController | null>(null);
  const [copyStatus, setCopyStatus] =
    useState<keyof typeof COPY_LABELS>("ready");
  const publish = (next: ConnectionsState) =>
    publishConnections(phase, setState, next);
  const connections = visibleConnections(state);
  const message = connectionMessage(state);
  const busy =
    state.kind === "loading" ||
    state.kind === "refreshing" ||
    state.kind === "disconnecting";

  useEffect(() => {
    const controller = new AbortController();
    reads.current = controller;
    const load = async () => {
      const result = await getMcpConnections(controller.signal);
      if (current())
        publishConnections(phase, setState, {
          kind: "ready",
          data: result,
          receipt: "loaded",
        });
    };
    const current = () =>
      reads.current === controller && !controller.signal.aborted;
    void load().catch((error: unknown) => {
      if (current())
        publishConnections(phase, setState, {
          kind:
            error instanceof McpConsentRequestError && error.status === 401
              ? "signin"
              : "unavailable",
        });
    });
    return () => {
      controller.abort();
      if (reads.current === controller) reads.current = null;
    };
  }, []);

  async function refresh(): Promise<void> {
    const previous = phase.current;
    const controller = reads.current;
    if (
      !controller ||
      controller.signal.aborted ||
      previous.kind === "loading" ||
      previous.kind === "refreshing" ||
      previous.kind === "disconnecting"
    )
      return;
    const current = () =>
      reads.current === controller && !controller.signal.aborted;
    publish({ kind: "refreshing", previous });
    try {
      const result = await getMcpConnections(controller.signal);
      if (current())
        publish({ kind: "ready", data: result, receipt: "refreshed" });
    } catch (error: unknown) {
      if (current())
        publish({
          kind:
            error instanceof McpConsentRequestError && error.status === 401
              ? "signin"
              : "read-uncertain",
        });
    }
  }

  async function disconnect(grantId: string): Promise<void> {
    const current = phase.current;
    const controller = reads.current;
    if (
      current.kind !== "ready" ||
      !controller ||
      controller.signal.aborted ||
      !current.data.connections.some(
        (connection) => connection["grant_id"] === grantId,
      )
    )
      return;
    const mounted = () =>
      reads.current === controller && !controller.signal.aborted;
    publish({ kind: "disconnecting", data: current.data, grantId });
    try {
      await disconnectMcpConnection(grantId, current.data.csrf);
      if (mounted()) publish({ kind: "disconnected" });
    } catch {
      if (mounted()) publish({ kind: "disconnect-uncertain" });
    }
  }

  const resource = `${API_URL}/mcp`;
  const command = `codex mcp add sarj-wiki --url ${quoteShell(resource)} && codex mcp login sarj-wiki`;

  async function copyCommand(): Promise<void> {
    try {
      await navigator.clipboard.writeText(command);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("unavailable");
    }
  }

  return (
    <section className="w-full space-y-6">
      <h1 className="text-2xl font-semibold">App connections</h1>
      <p className="text-muted-foreground" role="status">
        {message}
      </p>
      {state.kind === "signin" ? (
        <Button asChild>
          <Link
            href={{
              pathname: "/login",
              query: { redirect: "/mcp/connections" },
            }}
          >
            Sign in to Wiki
          </Link>
        </Button>
      ) : null}
      {connections !== null ? (
        <>
          <section className="bg-card space-y-3 rounded-xl border p-6">
            <h2 className="text-lg font-semibold">Connect Codex</h2>
            <p className="text-muted-foreground text-sm">
              Run this in your terminal, then review access in Wiki. Reading is
              the default; document changes need your separate approval.
            </p>
            <pre className="bg-muted overflow-x-auto rounded-md p-3 text-sm">
              <code>{command}</code>
            </pre>
            <Button onClick={() => void copyCommand()} variant="outline">
              {COPY_LABELS[copyStatus]}
            </Button>
            <p className="text-muted-foreground text-sm">
              If sarj-wiki is already configured, inspect that entry before
              replacing it. No API key or Google token needs to be copied.
            </p>
          </section>
          <h2 className="text-lg font-semibold">Your active connections</h2>
          {connections.connections.length === 0 ? (
            <p>No active app connections.</p>
          ) : (
            <ul className="bg-card divide-y rounded-xl border px-6">
              {connections.connections.map((connection) => (
                <li
                  className="flex flex-wrap items-center justify-between gap-4 py-5"
                  key={connection["grant_id"]}
                >
                  <div className="min-w-0 space-y-1">
                    <p className="text-sm wrap-anywhere">
                      Client ID: <bdi>{connection["client_id"]}</bdi>
                    </p>
                    <p className="font-semibold">
                      {connection.capabilities.includes("wiki:transform")
                        ? "Read and edit access"
                        : "Read-only access"}
                    </p>
                    <p className="text-muted-foreground text-sm">
                      Expires{" "}
                      <time dateTime={connection["expires_at"]}>
                        {new Date(
                          connection["expires_at"],
                        ).toLocaleDateString()}
                      </time>
                    </p>
                  </div>
                  <Button
                    disabled={busy}
                    onClick={() => void disconnect(connection["grant_id"])}
                    variant="outline"
                  >
                    Disconnect
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
      <Button disabled={busy} onClick={() => void refresh()} variant="outline">
        Refresh status
      </Button>
    </section>
  );
}

function quoteShell(value: string): string {
  return `'${value.replaceAll("'", String.raw`'\''`)}'`;
}

function visibleConnections(state: ConnectionsState): Connections | null {
  switch (state.kind) {
    case "ready":
    case "disconnecting":
      return state.data;
    case "refreshing":
      return state.previous.kind === "ready" ? state.previous.data : null;
    case "loading":
    case "signin":
    case "unavailable":
    case "disconnected":
    case "disconnect-uncertain":
    case "read-uncertain":
      return null;
    default:
      return unreachable(state);
  }
}

function connectionMessage(current: ConnectionsState): string {
  const state = current.kind === "refreshing" ? current.previous : current;
  switch (state.kind) {
    case "loading":
      return "Checking Wiki connections…";
    case "disconnecting":
      return "Disconnecting this app…";
    case "ready":
      return state.receipt === "loaded"
        ? "Only these app connections are affected when you disconnect. Your Wiki sign-in stays active."
        : "Connection status refreshed.";
    case "signin":
    case "unavailable":
      return "Wiki connections are unavailable. Sign in if needed, or try again when the integration is enabled.";
    case "disconnected":
      return "Disconnected. Existing admitted document jobs may still finish. Refresh to review your remaining connections.";
    case "disconnect-uncertain":
      return "The disconnect result is uncertain. Refresh status before trying again.";
    case "read-uncertain":
      return "Could not confirm connection status. Check again before retrying a disconnect.";
    default:
      return unreachable(state);
  }
}

function unreachable(value: never): never {
  throw new Error(`Unsupported connections state: ${String(value)}`);
}

function publishConnections(
  phase: RefObject<ConnectionsState>,
  setState: Dispatch<SetStateAction<ConnectionsState>>,
  next: ConnectionsState,
): void {
  phase.current = next;
  setState(next);
}
