import "server-only";

import { cookies } from "next/headers";

import { API_URL } from "@/lib/env";
import { createClient, createConfig } from "@/lib/generated/client";
import { makeWikiApi } from "@/lib/wiki-api";

const serverClient = createClient(createConfig({ baseUrl: API_URL, cache: "no-store" }));

serverClient.interceptors.request.use(async (request) => {
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) {
    throw new ServerApiError(401);
  }
  request.headers.set("Authorization", `Bearer ${token}`);
  return request;
});

serverClient.interceptors.response.use((response) => {
  if (!response.ok) {
    throw new ServerApiError(response.status);
  }
  return response;
});

export class ServerApiError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Server API error: ${status}`);
    this.status = status;
    this.name = "ServerApiError";
  }
}

export const serverWikiApi = makeWikiApi(serverClient);
