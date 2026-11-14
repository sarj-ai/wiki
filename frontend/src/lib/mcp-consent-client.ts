"use client";

import { z } from "zod";

import { boundedText } from "@/lib/bounded-text";
import { discardStream } from "@/lib/discard-stream";
import { API_URL } from "@/lib/env";

const IntentSchema = z.string().regex(/^wm_intent_[A-Za-z0-9_-]{43}$/);
const StatusSchema = z.enum(["pending", "approved", "denied", "expired"]);
const CapabilitySchema = z.enum(["wiki:read", "wiki:transform"]);
const ConsentCommandSchema = z.enum([
  "deny",
  "approve_read",
  "approve_transform",
]);
export type McpConsentCommand = z.output<typeof ConsentCommandSchema>;
const ViewSchema = z.object({
  "account_email": z.email(),
  "client_id": z.string().min(1).max(255),
  "client_name": z.string().max(256),
  csrf: IntentSchema,
  scopes: z.array(CapabilitySchema).max(2),
  status: StatusSchema,
});
const RecoverySchema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("pending"),
    "restart_required": z.literal(false),
  }),
  z.object({
    status: z.literal("approved"),
    "restart_required": z.literal(true),
  }),
  z.object({
    status: z.literal("denied"),
    "restart_required": z.literal(true),
  }),
  z.object({
    status: z.literal("expired"),
    "restart_required": z.literal(true),
  }),
]);
const DecisionSchema = z.object({ "redirect_uri": z.url() });
const GrantIdSchema = z.uuid();

const MAX_RESPONSE_BYTES = 256 * 1024;
const ConnectionsSchema = z.object({
  connections: z
    .array(
      z.object({
        capabilities: z.array(CapabilitySchema).max(2),
        "client_id": z.string().min(1).max(255),
        "expires_at": z.iso.datetime({ offset: true }),
        "grant_id": GrantIdSchema,
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

async function request(
  url: string,
  options?: RequestInit,
  signal?: AbortSignal,
): Promise<unknown> {
  const deadline = AbortSignal.timeout(15_000);
  const requestSignal =
    signal === undefined ? deadline : AbortSignal.any([deadline, signal]);
  requestSignal.throwIfAborted();
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "include",
    redirect: "error",
    referrerPolicy: "no-referrer",
    ...options,
    signal: requestSignal,
  });
  if (requestSignal.aborted) {
    discardStream(response.body);
    requestSignal.throwIfAborted();
  }
  if (!response.ok) {
    discardStream(response.body);
    throw new McpConsentRequestError(response.status);
  }
  try {
    const payload: unknown = JSON.parse(
      await boundedText(response.body, MAX_RESPONSE_BYTES, requestSignal),
    );
    requestSignal.throwIfAborted();
    return payload;
  } catch {
    requestSignal.throwIfAborted();
    throw new McpConsentRequestError(502);
  }
}

export async function getMcpConsent(
  intent: string,
  signal?: AbortSignal,
): Promise<McpConsentView> {
  return ViewSchema.parse(await request(endpoint(intent), undefined, signal));
}

export async function recoverMcpConsent(intent: string, signal?: AbortSignal) {
  return RecoverySchema.parse(
    await request(`${endpoint(intent)}/status`, undefined, signal),
  );
}

export async function getMcpConnections(
  signal?: AbortSignal,
): Promise<McpConnections> {
  return ConnectionsSchema.parse(
    await request(`${API_URL}/integrations/mcp/connections`, undefined, signal),
  );
}

export async function disconnectMcpConnection(
  grantId: string,
  csrf: string,
): Promise<void> {
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
  discardStream(response.body);
  if (response.status !== 204)
    throw new McpConsentRequestError(response.status);
}

export async function decideMcpConsent(
  intent: string,
  csrf: string,
  decision: McpConsentCommand,
): Promise<string> {
  const result = DecisionSchema.parse(
    await request(endpoint(intent), {
      body: JSON.stringify({
        command: ConsentCommandSchema.parse(decision),
        csrf: IntentSchema.parse(csrf),
      }),
      headers: { "Content-Type": "application/json" },
      method: "POST",
    }),
  );
  const callback = new URL(result["redirect_uri"]);
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(
    callback.hostname,
  );
  if (
    callback.username ||
    callback.password ||
    callback.hash ||
    (callback.protocol !== "https:" &&
      !(callback.protocol === "http:" && loopback && callback.port))
  )
    throw new McpConsentRequestError(502);
  return result["redirect_uri"];
}
