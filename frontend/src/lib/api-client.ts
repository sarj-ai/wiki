import { z } from "zod";

import { ApiError, RATE_LIMIT_MESSAGE, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { API_URL } from "@/lib/env";
import type { Client } from "@/lib/generated/client";
import { client } from "@/lib/generated/client.gen";
import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";

type AnyOperation = (options: never) => Promise<unknown>;

type OperationOptions<Op extends AnyOperation> = Omit<
  NonNullable<Parameters<Op>[0]>,
  "client" | "throwOnError"
>;

const replayCache = new WeakMap<Request, Request>();

client.setConfig({
  baseUrl: API_URL,
  credentials: "include",
  throwOnError: true,
});

const SESSION_EXP_SKEW_MS = 30_000;
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
const ZOperationResult = z.object({ data: z.unknown().optional() });

function sessionExpiresAt(): null | number {
  if (typeof document === "undefined") return null;
  const match = SESSION_EXP_COOKIE.exec(document.cookie);
  return match ? Number(match[1]) : null;
}

client.interceptors.request.use(async (request) => {
  if (!request.headers.has("X-Request-ID")) {
    request.headers.set("X-Request-ID", crypto.randomUUID());
  }
  const exp = sessionExpiresAt();
  if (exp !== null && Date.now() > exp - SESSION_EXP_SKEW_MS) {
    const refresh = await deduplicatedRefresh();
    if (refresh === "unavailable") {
      throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
    }
    if (refresh === "unauthorized") {
      notifyTerminalUnauthorized();
      throw new ApiError(401, "Your session has expired");
    }
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    replayCache.set(request, request.clone());
  }
  return request;
});

client.interceptors.response.use(async (response, request) => {
  let settled = response;
  if (settled.status === 401 && !request.url.endsWith("/auth/refresh")) {
    const refresh = await deduplicatedRefresh();
    if (refresh === "refreshed") {
      settled = await fetch(replayCache.get(request) ?? request);
    }
    if (refresh === "unavailable") {
      throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
    }
  }
  if (!settled.ok) {
    if (settled.status === 401) notifyTerminalUnauthorized();
    if (settled.status === 429) throw new ApiError(429, RATE_LIMIT_MESSAGE);
    const message = await readErrorMessage(settled);
    throw new ApiError(settled.status, message ?? `API error: ${settled.status}`);
  }
  return settled;
});

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
    };
    const result: unknown = await Reflect.apply(operation, undefined, [merged]);
    if (schema === undefined) return;
    return schema.parse(ZOperationResult.parse(result).data);
  }
  return call;
}

export { ApiError } from "@/lib/api-error";

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
      const first = detail[0];
      if (first && typeof first.msg === "string") return first.msg;
    }
    return null;
  } catch {
    return null;
  }
}
