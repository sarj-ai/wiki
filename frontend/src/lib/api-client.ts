import { type z } from "zod";

import { API_URL } from "@/lib/env";
import { type Client } from "@/lib/generated/client";
import { client } from "@/lib/generated/client.gen";

type AnyOperation = (options: never) => Promise<unknown>;

type OperationOptions<Op extends AnyOperation> = Omit<
  NonNullable<Parameters<Op>[0]>,
  "client" | "throwOnError"
>;

const replayCache = new WeakMap<Request, Request>();

let refreshPromise: Promise<boolean> | null = null;

client.setConfig({
  baseUrl: API_URL,
  credentials: "include",
  throwOnError: true,
});

client.interceptors.request.use((request) => {
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

async function readErrorMessage(response: Response): Promise<string | null> {
  try {
    const text = await response.clone().text();
    if (!text) return null;
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object") return null;
    const detail = (parsed as { detail?: unknown }).detail;
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

function deduplicatedRefresh(): Promise<boolean> {
  refreshPromise ??= rawRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function rawRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
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
  ): Promise<z.output<S> | void> {
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
