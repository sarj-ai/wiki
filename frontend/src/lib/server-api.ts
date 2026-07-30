import "server-only";

import { cookies as requestCookies } from "next/headers";

import { API_URL } from "@/lib/env";
import { createClient, createConfig } from "@/lib/generated/client";
import { ServerApiError } from "@/lib/server-api-error";
import { makeWikiApi } from "@/lib/wiki-api";

const serverClient = createClient(createConfig({ baseUrl: API_URL, cache: "no-store" }));

serverClient.interceptors.request.use(async (request) => {
  const cookies = await requestCookies();
  const token = cookies.get("access_token")?.value;
  if (token === undefined) {
    throw new ServerApiError(401);
  }
  request.headers.set("cookie", `access_token=${token}`);
  return request;
});

serverClient.interceptors.response.use((response) => {
  if (!response.ok) {
    throw new ServerApiError(response.status);
  }
  return response;
});

export const serverWikiApi = makeWikiApi(serverClient);
