import { z } from "zod";

import { ApiError, RATE_LIMIT_MESSAGE, SESSION_REFRESH_UNAVAILABLE_MESSAGE } from "@/lib/api-error";
import { boundedErrorText } from "@/lib/bounded-error-text";
import { captureBrowserSession } from "@/lib/browser-session";
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
const REQUEST_SESSIONS = new WeakMap<Request, AbortSignal>();
const SESSION_META = Symbol("browserSession");

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

client.interceptors.request.use(async (request, options) => {
  const session = requestSession(options);
  session.throwIfAborted();
  request.signal.throwIfAborted();
  // Reads belong to the mounted session. An already-dispatched mutation can
  // finish, while its retired owner is prevented from refreshing or replaying.
  const ownedRequest =
    request.method === "GET" || request.method === "HEAD"
      ? new Request(request, { signal: AbortSignal.any([request.signal, session]) })
      : request;
  REQUEST_SESSIONS.set(ownedRequest, session);
  if (!request.headers.has("X-Request-ID")) {
    ownedRequest.headers.set("X-Request-ID", crypto.randomUUID());
  }
  if (sessionNeedsRefresh()) {
    const refresh = await deduplicatedRefresh(request.signal, session);
    session.throwIfAborted();
    request.signal.throwIfAborted();
    if (refresh === "unavailable") {
      throw new ApiError(503, SESSION_REFRESH_UNAVAILABLE_MESSAGE);
    }
    if (refresh === "unauthorized") {
      notifyTerminalUnauthorized(session);
      throw new ApiError(401, "Your session has expired");
    }
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    REPLAY_CACHE.set(ownedRequest, ownedRequest.clone());
  }
  return ownedRequest;
});

client.interceptors.response.use(async (response, request) => {
  const session = REQUEST_SESSIONS.get(request) ?? captureBrowserSession();
  if (session.aborted || request.signal.aborted) {
    discardStream(response.body);
    session.throwIfAborted();
    request.signal.throwIfAborted();
  }
  let settled = response;
  if (settled.status === 401 && !request.url.endsWith("/auth/refresh")) {
    try {
      const refresh = await deduplicatedRefresh(request.signal, session);
      session.throwIfAborted();
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
  if (session.aborted || request.signal.aborted) {
    discardStream(settled.body);
    session.throwIfAborted();
    request.signal.throwIfAborted();
  }
  if (!settled.ok) {
    if (settled.status === 401) notifyTerminalUnauthorized(session);
    if (settled.status === 429) {
      discardStream(settled.body);
      throw new ApiError(429, RATE_LIMIT_MESSAGE);
    }
    const message = await readErrorMessage(settled, AbortSignal.any([session, request.signal]));
    session.throwIfAborted();
    request.signal.throwIfAborted();
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
    // SDK validation/auth can yield before request interceptors run. Carry the
    // calling session through SDK metadata, never through an HTTP header.
    const session = clientOverride === undefined ? captureBrowserSession() : undefined;
    session?.throwIfAborted();
    const merged: Record<PropertyKey, unknown> = {
      ...options,
      throwOnError: true,
    };
    if (session !== undefined) {
      const previousMeta: unknown = "meta" in options ? options["meta"] : undefined;
      const meta: Record<PropertyKey, unknown> = {};
      if (typeof previousMeta === "object" && previousMeta !== null) {
        Object.assign(meta, previousMeta);
      }
      meta[SESSION_META] = session;
      merged["meta"] = meta;
    }
    if (clientOverride !== undefined) merged["client"] = clientOverride;
    let result: unknown;
    try {
      result = await Reflect.apply(operation, undefined, [merged]);
    } catch (error: unknown) {
      session?.throwIfAborted();
      throw error;
    }
    session?.throwIfAborted();
    if (schema === undefined) return;
    return schema.parse(OperationResultSchema.parse(result).data);
  }
  return call;
}

export { ApiError } from "@/lib/api-error";

function requestSession(options: object): AbortSignal {
  if (
    "meta" in options &&
    typeof options.meta === "object" &&
    options.meta !== null &&
    Object.hasOwn(options.meta, SESSION_META)
  ) {
    const session: unknown = Reflect.get(options.meta, SESSION_META);
    if (session instanceof AbortSignal) return session;
  }
  return captureBrowserSession();
}

async function readErrorMessage(response: Response, signal: AbortSignal): Promise<null | string> {
  try {
    const text = await boundedErrorText(response.body, signal);
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
