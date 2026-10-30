import "server-only";

import { cookies, headers } from "next/headers";

import { fetchBackend } from "@/lib/clients/fetch-backend";
import { discardStream } from "@/lib/discard-stream";
import { API_URL } from "@/lib/env";
import { createClient, createConfig } from "@/lib/generated/client";
import { makeWikiApi } from "@/lib/wiki-api";

const serverClient = createClient(
  createConfig({ baseUrl: API_URL, cache: "no-store", fetch: fetchBackend, redirect: "manual" }),
);

serverClient.interceptors.request.use(async (request) => {
  const [cookieStore, requestHeaders] = await Promise.all([cookies(), headers()]);
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) {
    throw new ServerApiError(401);
  }
  request.headers.set("Authorization", `Bearer ${token}`);
  request.headers.set("X-Request-ID", requestHeaders.get("X-Request-ID") ?? crypto.randomUUID());
  return request;
});

serverClient.interceptors.response.use((response) => {
  if (!response.ok) {
    discardStream(response.body);
    throw new ServerApiError(response.status);
  }
  return response;
});

export class ServerApiError extends Error {
  readonly status: number;

  constructor(status: number, options: ErrorOptions = {}) {
    super(`Server API error: ${status}`, options);
    this.status = status;
    this.name = "ServerApiError";
  }
}

export const serverWikiApi = makeWikiApi(serverClient);
