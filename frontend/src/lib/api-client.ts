import { z } from "zod";

import { API_URL } from "@/lib/env";
import type { Client } from "@/lib/generated/client";
import { client } from "@/lib/generated/client.gen";

type AnyOperation = (options: never) => Promise<unknown>;

type OperationOptions<Op extends AnyOperation> = Omit<
  NonNullable<Parameters<Op>[0]>,
  "client" | "throwOnError"
>;

const replayCache = new WeakMap<Request, Request>();

let refreshPromise: null | Promise<boolean> = null;

client.setConfig({
  baseUrl: API_URL,
  credentials: "include",
  throwOnError: true,
});

const SESSION_EXP_SKEW_MS = 30_000;
const REFRESH_TIMEOUT_MS = 10_000;

const SESSION_EXP_COOKIE = /(?:^|;\s*)session_exp=(\d+)/;
const ZErrorDetail = z.object({
  detail: z
    .union([
      z.string(),
      z.array(
        z.object({
          msg: z.string().optional(),
        }),
      ),
    ])
    .optional(),
});

function sessionExpiresAt(): null | number {
  if (typeof document === "undefined") return null;
  const match = SESSION_EXP_COOKIE.exec(document.cookie);
  return match ? Number(match[1]) : null;
}

client.interceptors.request.use(async (request) => {
  const exp = sessionExpiresAt();
  if (exp !== null && Date.now() > exp - SESSION_EXP_SKEW_MS) {
    await deduplicatedRefresh();
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    replayCache.set(request, request.clone());
  }
  return request;
});

client.interceptors.response.use(async (response, request) => {
  let settled = response;
  if (settled.status === 401 && !request.url.endsWith("/auth/refresh")) {
    const refreshed = await deduplicatedRefresh();
    if (refreshed) {
      settled = await fetch(replayCache.get(request) ?? request);
    }
  }
  if (!settled.ok) {
    const message = await readErrorMessage(settled);
    throw new ApiError(settled.status, message ?? `API error: ${settled.status}`);
  }
  return settled;
});

async function readErrorMessage(response: Response): Promise<null | string> {
  try {
    const text = await response.clone().text();
    if (!text) return null;
    const parsed: unknown = JSON.parse(text);
    const payload = ZErrorDetail.safeParse(parsed);
    if (!payload.success) return null;
    const { detail } = payload.data;
    if (typeof detail === "string" && detail.trim().length > 0) return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first.msg === "string") return first.msg;
    }
    return null;
  } catch {
    return null;
  }
}

export async function deduplicatedRefresh(): Promise<boolean> {
  refreshPromise ??= rawRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function rawRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/auth/refresh`, {
      credentials: "include",
      method: "POST",
      signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export function makeCall(clientOverride?: Client) {
  function call<Op extends AnyOperation, S extends z.ZodType>(
    operation: Op,
    options: OperationOptions<Op>,
    schema: S,
  ): Promise<z.output<S>>;
  function call<Op extends AnyOperation>(
    operation: Op,
    options: OperationOptions<Op>,
  ): Promise<void>;
  async function call<Op extends AnyOperation, S extends z.ZodType>(
    operation: Op,
    options: OperationOptions<Op>,
    schema?: S,
  ): Promise<void | z.output<S>> {
    const merged = {
      ...options,
      ...(clientOverride === undefined ? {} : { client: clientOverride }),
      throwOnError: true,
    } as Parameters<Op>[0];
    const result = (await operation(merged)) as { data?: unknown };
    if (schema === undefined) return;
    return schema.parse(result.data);
  }
  return call;
}
