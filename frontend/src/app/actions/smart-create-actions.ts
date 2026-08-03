"use server";

import { getCloudflareContext } from "@opennextjs/cloudflare";
import { cookies, headers } from "next/headers";

import type { ActionResult } from "@/lib/action-result";
import { callSmartCreateApi } from "@/lib/smart-create-server-client";

type SmartOperation = "create" | "proposal";

const CONFIG = {
  create: {
    binding: "SMART_CREATE_RATE_LIMITER",
    upstreamPath: "/files/smart",
  },
  proposal: {
    binding: "SMART_PROPOSAL_RATE_LIMITER",
    upstreamPath: "/files/smart-proposal",
  },
} as const satisfies Record<SmartOperation, { binding: keyof CloudflareEnv; upstreamPath: string }>;

export async function createSmartFileAction(
  body: string,
  idempotencyKey: string,
): Promise<ActionResult<string>> {
  return runSmartOperation("create", body, idempotencyKey);
}

export async function proposeSmartFileAction(body: string): Promise<ActionResult<string>> {
  return runSmartOperation("proposal", body);
}

async function runSmartOperation(
  operation: SmartOperation,
  body: string,
  idempotencyKey?: string,
): Promise<ActionResult<string>> {
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) return failure(401, "Unauthorized");

  const config = CONFIG[operation];
  const context = getCloudflareContext();
  const limiter = context.env[config.binding];
  const requestHeaders = await headers();
  const clientAddress = requestHeaders.get("cf-connecting-ip");
  if (limiter === undefined || clientAddress === null) {
    if (process.env.NODE_ENV === "production") {
      return failure(503, "Smart Create is temporarily unavailable");
    }
  } else {
    const limit = await limiter.limit({ key: clientAddress });
    if (!limit.success) return failure(429, "Too many requests. Try again in a minute.");
  }

  const response = await callSmartCreateApi(config.upstreamPath, body, token, idempotencyKey);
  if (!response.ok) {
    return failure(
      response.status,
      response.error ?? "Smart Create could not complete the request",
    );
  }
  return { data: response.body, ok: true };
}

function failure(status: number, error: string): ActionResult<never> {
  return { error, ok: false, status };
}
