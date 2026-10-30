"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { McpConsent } from "@/components/mcp/mcp-consent";
import { Button } from "@/components/ui/button";
import {
  decideMcpConsent,
  getMcpConsent,
  McpConsentRequestError,
  type McpConsentView,
  recoverMcpConsent,
} from "@/lib/mcp-consent-client";

type LoadState =
  | { intent: string; kind: "loading" }
  | { intent: string; kind: "ready"; signal: AbortSignal; view: McpConsentView }
  | { intent: string; kind: "signin" }
  | { intent: string; kind: "unavailable" };

export function McpConsentLoader({ intent }: { intent: string }) {
  const [state, setState] = useState<LoadState>({ intent, kind: "loading" });

  useEffect(() => {
    let current = true;
    const controller = new AbortController();
    const load = async () => {
      const view = await getMcpConsent(intent, controller.signal);
      if (current) setState({ intent, kind: "ready", signal: controller.signal, view });
    };
    void load().catch((error: unknown) => {
      if (current)
        setState({
          intent,
          kind:
            error instanceof McpConsentRequestError && error.status === 401
              ? "signin"
              : "unavailable",
        });
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [intent]);

  if (
    state.intent !== intent ||
    state.kind === "loading" ||
    (state.kind === "ready" && state.signal.aborted)
  )
    return <p role="status">Checking this connection request…</p>;

  if (state.kind === "ready") {
    const { signal, view } = state;
    return (
      <McpConsent
        accountEmail={view.account_email}
        clientName={view.client_name}
        initialStatus={view.status}
        key={`${intent}:${view.csrf}`}
        onDecision={async (decision) => {
          const callback = await decideMcpConsent(intent, view.csrf, decision);
          window.location.assign(callback);
          return decision.approve ? "approved" : "denied";
        }}
        onRecover={async () => {
          const recovered = await recoverMcpConsent(intent, signal);
          return recovered.status;
        }}
        requestsTransform={view.scopes.includes("wiki:transform")}
      />
    );
  }

  return (
    <section className="bg-card mx-auto w-full max-w-lg space-y-4 rounded-xl border p-6">
      <h1 className="text-2xl font-semibold">
        {state.kind === "signin" ? "Sign in to review access" : "Connection request unavailable"}
      </h1>
      <p className="text-muted-foreground" role="status">
        {state.kind === "signin"
          ? "Sign in to Wiki in this browser. You will review permissions before granting access."
          : "This request may have expired or Wiki connections may not be enabled. Start a new connection from your app."}
      </p>
      {state.kind === "signin" && (
        <Button asChild>
          <Link
            href={{
              pathname: "/login",
              query: { redirect: `/mcp/consent?intent=${encodeURIComponent(intent)}` },
            }}
          >
            Sign in to Wiki
          </Link>
        </Button>
      )}
    </section>
  );
}
