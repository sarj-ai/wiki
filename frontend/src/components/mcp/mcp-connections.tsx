"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { API_URL } from "@/lib/env";
import {
  disconnectMcpConnection,
  getMcpConnections,
  type McpConnections as Connections,
  McpConsentRequestError,
} from "@/lib/mcp-consent-client";

export function McpConnections() {
  const [connections, setConnections] = useState<Connections | null>(null);
  const [message, setMessage] = useState("Checking Wiki connections…");
  const [signin, setSignin] = useState(false);
  const [busy, setBusy] = useState(true);
  const locked = useRef(true);
  const reads = useRef<AbortController | null>(null);
  const [copyMessage, setCopyMessage] = useState("Copy setup command");

  useEffect(() => {
    let current = true;
    const controller = new AbortController();
    reads.current = controller;
    const load = async () => {
      const result = await getMcpConnections(controller.signal);
      if (current) {
        setConnections(result);
        setMessage(
          "Only these app connections are affected when you disconnect. Your Wiki sign-in stays active.",
        );
      }
    };
    void load()
      .catch((error: unknown) => {
        if (current) {
          setSignin(error instanceof McpConsentRequestError && error.status === 401);
          setMessage(
            "Wiki connections are unavailable. Sign in if needed, or try again when the integration is enabled.",
          );
        }
      })
      .finally(() => {
        if (current) {
          locked.current = false;
          setBusy(false);
        }
      });
    return () => {
      current = false;
      controller.abort();
      if (reads.current === controller) reads.current = null;
    };
  }, []);

  function unlock(): void {
    locked.current = false;
    setBusy(false);
  }

  async function refresh(): Promise<void> {
    const controller = reads.current;
    if (locked.current || !controller) return;
    const current = () => reads.current === controller && !controller.signal.aborted;
    if (!current()) return;
    locked.current = true;
    setBusy(true);
    try {
      const result = await getMcpConnections(controller.signal);
      if (!current()) return;
      setConnections(result);
      setSignin(false);
      setMessage("Connection status refreshed.");
    } catch {
      if (current())
        setMessage(
          "Could not confirm connection status. Check again before retrying a disconnect.",
        );
    } finally {
      if (current()) unlock();
    }
  }

  async function disconnect(grantId: string): Promise<void> {
    if (locked.current || connections === null) return;
    locked.current = true;
    setBusy(true);
    try {
      await disconnectMcpConnection(grantId, connections.csrf);
      setConnections(null);
      setMessage(
        "Disconnected. Existing admitted document jobs may still finish. Refresh to review your remaining connections.",
      );
    } catch {
      setConnections(null);
      setMessage("The disconnect result is uncertain. Refresh status before trying again.");
    } finally {
      unlock();
    }
  }

  const resource = `${API_URL}/mcp`;
  const command = `codex mcp add sarj-wiki --url ${quoteShell(resource)} && codex mcp login sarj-wiki`;

  async function copyCommand(): Promise<void> {
    try {
      await navigator.clipboard.writeText(command);
      setCopyMessage("Setup command copied");
    } catch {
      setCopyMessage("Copy unavailable; select the command above");
    }
  }

  return (
    <section className="w-full space-y-6">
      <h1 className="text-2xl font-semibold">App connections</h1>
      <p className="text-muted-foreground" role="status">
        {message}
      </p>
      {signin ? (
        <Button asChild>
          <Link href={{ pathname: "/login", query: { redirect: "/mcp/connections" } }}>
            Sign in to Wiki
          </Link>
        </Button>
      ) : null}
      {connections !== null ? (
        <>
          <section className="bg-card space-y-3 rounded-xl border p-6">
            <h2 className="text-lg font-semibold">Connect Codex</h2>
            <p className="text-muted-foreground text-sm">
              Run this in your terminal, then review access in Wiki. Reading is the default;
              document changes need your separate approval.
            </p>
            <pre className="bg-muted overflow-x-auto rounded-md p-3 text-sm">
              <code>{command}</code>
            </pre>
            <Button onClick={() => void copyCommand()} variant="outline">
              {copyMessage}
            </Button>
            <p className="text-muted-foreground text-sm">
              If sarj-wiki is already configured, inspect that entry before replacing it. No API key
              or Google token needs to be copied.
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
                  key={connection.grant_id}
                >
                  <div className="min-w-0 space-y-1">
                    <p className="text-sm wrap-anywhere">
                      Client ID: <bdi>{connection.client_id}</bdi>
                    </p>
                    <p className="font-semibold">
                      {connection.capabilities.includes("wiki:transform")
                        ? "Read and edit access"
                        : "Read-only access"}
                    </p>
                    <p className="text-muted-foreground text-sm">
                      Expires{" "}
                      <time dateTime={connection.expires_at}>
                        {new Date(connection.expires_at).toLocaleDateString()}
                      </time>
                    </p>
                  </div>
                  <Button
                    disabled={busy}
                    onClick={() => void disconnect(connection.grant_id)}
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
