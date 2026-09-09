"use server";

import { getCloudflareContext } from "@opennextjs/cloudflare";
import { cookies, headers } from "next/headers";

import type { ActionErrorCode, ActionResult } from "@/lib/action-result";
import { RATE_LIMIT_MESSAGE } from "@/lib/api-error";
import { callSmartCreateApi, readSmartCreateApi } from "@/lib/smart-create-server-client";

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

export async function createDocumentTransformAction(
  body: string,
  idempotencyKey: string,
): Promise<ActionResult<string>> {
  return runSmartOperation("create", body, idempotencyKey, "/document-transforms");
}

export async function getDocumentTransformCapabilitiesAction(): Promise<ActionResult<string>> {
  return runSmartRead("/document-transforms/capabilities");
}

export async function getCurrentDocumentPolishAction(
  fileId: string,
  mode: "both" | "format" | "unslop",
): Promise<ActionResult<string>> {
  return runSmartRead(
    `/document-transforms/current?file_id=${encodeURIComponent(fileId)}&mode=${encodeURIComponent(mode)}`,
  );
}

export async function proposeSmartFileAction(body: string): Promise<ActionResult<string>> {
  return runSmartOperation("proposal", body);
}

export async function proposeFileReorganizationAction(
  fileId: string,
): Promise<ActionResult<string>> {
  return runSmartOperation(
    "proposal",
    "{}",
    undefined,
    `/files/${encodeURIComponent(fileId)}/reorganize-proposal`,
  );
}

export async function getSmartCreateTaskAction(taskId: string): Promise<ActionResult<string>> {
  return runSmartRead(`/tasks/${encodeURIComponent(taskId)}`);
}

export async function listSmartTemplatesAction(refresh: boolean): Promise<ActionResult<string>> {
  return runSmartRead(`/templates?refresh=${String(refresh)}`);
}

async function runSmartRead(path: string): Promise<ActionResult<string>> {
  const cookieStore = await cookies();
  const token = cookieStore.get("access_token")?.value;
  if (token === undefined) return failure(401, "Unauthorized");
  const response = await readSmartCreateApi(path, token);
  if (!response.ok) {
    return failure(
      response.status,
      response.status === 429
        ? RATE_LIMIT_MESSAGE
        : (response.error ?? "Smart Create could not complete the request"),
    );
  }
  return { data: response.body, ok: true };
}

async function runSmartOperation(
  operation: SmartOperation,
  body: string,
  idempotencyKey?: string,
  upstreamPath?: string,
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
    if (!limit.success) return failure(429, RATE_LIMIT_MESSAGE);
  }

  const response = await callSmartCreateApi(
    upstreamPath ?? config.upstreamPath,
    body,
    token,
    idempotencyKey,
  );
  if (!response.ok) {
    return failure(
      response.status,
      response.status === 429
        ? RATE_LIMIT_MESSAGE
        : (response.error ?? "Smart Create could not complete the request"),
      response.errorCode,
    );
  }
  return {
    data: response.body,
    ...(response.idempotencyReplayed === undefined
      ? {}
      : { replayed: response.idempotencyReplayed }),
    ok: true,
  };
}

function failure(status: number, error: string, code?: ActionErrorCode): ActionResult<never> {
  return { ...(code === undefined ? {} : { code }), error, ok: false, status };
}
