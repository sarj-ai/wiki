import "server-only";

import * as z from "zod";

import { API_URL } from "@/lib/env";

const ZErrorResponse = z.object({ detail: z.string().optional() });
const UPSTREAM_TIMEOUT_MS = 30_000;

export interface SmartUpstreamResponse {
  body: string;
  error?: string;
  ok: boolean;
  status: number;
}

export async function callSmartCreateApi(
  path: string,
  body: string,
  token: string,
  idempotencyKey?: string,
): Promise<SmartUpstreamResponse> {
  const headers = new Headers({
    "content-type": "application/json",
    cookie: `access_token=${token}`,
  });
  if (idempotencyKey !== undefined) headers.set("idempotency-key", idempotencyKey);

  try {
    const response = await fetch(`${API_URL}${path}`, {
      body,
      cache: "no-store",
      headers,
      method: "POST",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    const responseBody = await response.text();
    if (response.ok) return { body: responseBody, ok: true, status: response.status };
    const parsed = ZErrorResponse.safeParse(safeJson(responseBody));
    const error = parsed.success ? parsed.data.detail : undefined;
    return {
      body: responseBody,
      ...(error === undefined ? {} : { error }),
      ok: false,
      status: response.status,
    };
  } catch {
    return { body: "", error: "Smart Create could not reach the API", ok: false, status: 502 };
  }
}

export async function readSmartCreateApi(
  path: string,
  token: string,
): Promise<SmartUpstreamResponse> {
  try {
    const response = await fetch(`${API_URL}${path}`, {
      cache: "no-store",
      headers: { cookie: `access_token=${token}` },
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    const responseBody = await response.text();
    if (response.ok) return { body: responseBody, ok: true, status: response.status };
    const parsed = ZErrorResponse.safeParse(safeJson(responseBody));
    const error = parsed.success ? parsed.data.detail : undefined;
    return {
      body: responseBody,
      ...(error === undefined ? {} : { error }),
      ok: false,
      status: response.status,
    };
  } catch {
    return { body: "", error: "Smart Create could not reach the API", ok: false, status: 502 };
  }
}

function safeJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}
