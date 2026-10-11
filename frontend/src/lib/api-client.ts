import { z } from "zod";

import { ApiError, RATE_LIMIT_MESSAGE, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { discardStream } from "@/lib/discard-stream";
import { API_URL } from "@/lib/env";
import type { Client } from "@/lib/generated/client";
import { client } from "@/lib/generated/client.gen";
import { deduplicatedRefresh } from "@/lib/session-client";
import { notifyTerminalUnauthorized } from "@/lib/session-events";
import { sessionNeedsRefresh } from "@/lib/session-needs-refresh";

type AnyOperation = (options: never) => Promise<unknown>;

type OperationOptions<Op extends AnyOperation> = Omit<
  NonNullable<Parameters<Op>[0]>,
  "client" | "throwOnError"
>;

const REPLAY_CACHE = new WeakMap<Request, Request>();

client.setConfig({
  baseUrl: API_URL,
  credentials: "include",
  throwOnError: true,
});

const ErrorDetailSchema = z.object({
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
const OperationResultSchema = z.object({ data: z.unknown().optional() });

client.interceptors.request.use(async (request) => {
  request.signal.throwIfAborted();
  if (!request.headers.has("X-Request-ID")) {
    request.headers.set("X-Request-ID", crypto.randomUUID());
  }
  if (sessionNeedsRefresh()) {
    const refresh = await deduplicatedRefresh(request.signal);
    request.signal.throwIfAborted();
    if (refresh === "unavailable") {
      throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
    }
    if (refresh === "unauthorized") {
      notifyTerminalUnauthorized();
      throw new ApiError(401, "Your session has expired");
    }
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    REPLAY_CACHE.set(request, request.clone());
  }
  return request;
});

client.interceptors.response.use(async (response, request) => {
  request.signal.throwIfAborted();
  let settled = response;
  if (settled.status === 401 && !request.url.endsWith("/auth/refresh")) {
    try {
      const refresh = await deduplicatedRefresh(request.signal);
      request.signal.throwIfAborted();
      if (refresh === "refreshed") {
        discardStream(settled.body);
        settled = await fetch(REPLAY_CACHE.get(request) ?? request);
      }
      if (refresh === "unavailable") {
        throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
      }
    } catch (error: unknown) {
      discardStream(settled.body);
      throw error;
    }
  }
  if (!settled.ok) {
    if (settled.status === 401) notifyTerminalUnauthorized();
    if (settled.status === 429) {
      discardStream(settled.body);
      throw new ApiError(429, RATE_LIMIT_MESSAGE);
    }
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
    return schema.parse(OperationResultSchema.parse(result).data);
  }
  return call;
}

export { ApiError } from "@/lib/api-error";

async function readErrorMessage(response: Response): Promise<null | string> {
  try {
    const text = await response.text();
    if (!text) return null;
    const parsed: unknown = JSON.parse(text);
    const payload = ErrorDetailSchema.safeParse(parsed);
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
