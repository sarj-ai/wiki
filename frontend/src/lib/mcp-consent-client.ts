"use client";

import { z } from "zod";

import { API_URL } from "@/lib/env";

const IntentSchema = z.string().regex(/^wm_intent_[A-Za-z0-9_-]{43}$/);
const StatusSchema = z.enum(["pending", "approved", "denied", "expired"]);
const CapabilitySchema = z.enum(["wiki:read", "wiki:transform"]);
const ViewSchema = z.object({
  account_email: z.email(),
  client_id: z.string().min(1).max(255),
  client_name: z.string().max(256),
  csrf: IntentSchema,
  scopes: z.array(CapabilitySchema).max(2),
  status: StatusSchema,
});
const RecoverySchema = z.object({ restart_required: z.boolean(), status: StatusSchema });
const DecisionSchema = z.object({ redirect_uri: z.url() });
const GrantIdSchema = z.uuid();
const ConnectionsSchema = z.object({
  connections: z
    .array(
      z.object({
        capabilities: z.array(CapabilitySchema).max(2),
        client_id: z.string().min(1).max(255),
        expires_at: z.iso.datetime({ offset: true }),
        grant_id: GrantIdSchema,
      }),
    )
    .max(100),
  csrf: z.string().regex(/^[a-f0-9]{64}$/),
});

export type McpConsentView = z.output<typeof ViewSchema>;
export type McpConnections = z.output<typeof ConnectionsSchema>;

export class McpConsentRequestError extends Error {
  readonly status: number;

  constructor(status: number, options: ErrorOptions = {}) {
    super("Wiki connection request unavailable", options);
    this.name = "McpConsentRequestError";
    this.status = status;
  }
}

function endpoint(intent: string): string {
  return `${API_URL}/integrations/mcp/consent/${IntentSchema.parse(intent)}`;
}

async function request(url: string, options?: RequestInit): Promise<unknown> {
  // Browser-only: the API's host-only nonce must never be forwarded through a server action.
  // Do not automatically retry decisions after a transport or authentication failure.
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "include",
    redirect: "error",
    referrerPolicy: "no-referrer",
    signal: AbortSignal.timeout(15_000),
    ...options,
  });
  if (!response.ok) throw new McpConsentRequestError(response.status);
  return response.json();
}

export async function getMcpConsent(intent: string): Promise<McpConsentView> {
  return ViewSchema.parse(await request(endpoint(intent)));
}

export async function recoverMcpConsent(intent: string) {
  return RecoverySchema.parse(await request(`${endpoint(intent)}/status`));
}

export async function getMcpConnections(): Promise<McpConnections> {
  return ConnectionsSchema.parse(await request(`${API_URL}/integrations/mcp/connections`));
}

export async function disconnectMcpConnection(grantId: string, csrf: string): Promise<void> {
  const response = await fetch(
    `${API_URL}/integrations/mcp/connections/${GrantIdSchema.parse(grantId)}`,
    {
      cache: "no-store",
      credentials: "include",
      headers: { "X-MCP-CSRF": ConnectionsSchema.shape.csrf.parse(csrf) },
      method: "DELETE",
      redirect: "error",
      referrerPolicy: "no-referrer",
      signal: AbortSignal.timeout(15_000),
    },
  );
  if (response.status !== 204) throw new McpConsentRequestError(response.status);
}

export async function decideMcpConsent(
  intent: string,
  csrf: string,
  decision: { approve: boolean; transformApproved: boolean },
): Promise<string> {
  const result = DecisionSchema.parse(
    await request(endpoint(intent), {
      body: JSON.stringify({
        approve: decision.approve,
        csrf: IntentSchema.parse(csrf),
        transform_approved: decision.approve && decision.transformApproved,
      }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    }),
  );
  const callback = new URL(result.redirect_uri);
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(callback.hostname);
  if (
    callback.username ||
    callback.password ||
    callback.hash ||
    (callback.protocol !== "https:" &&
      !(callback.protocol === "http:" && loopback && callback.port))
  )
    throw new McpConsentRequestError(502);
  return result.redirect_uri;
}
