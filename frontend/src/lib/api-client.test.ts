import { type ResponsePromise } from "ky";
import { describe, expect, it } from "vitest";
import * as z from "zod";

import {
  ApiValidationError,
  rebuildForReplay,
  snakecaseJsonBody,
  snakecaseJsonString,
  validatedJson,
} from "@/lib/api-client";
import { zDriveSummary, zUserProfile } from "@/lib/generated/zod.gen";

function jsonRequest(url: string, body: unknown): Request {
  return new Request(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Pure transform — the actual camelCase→snake_case conversion the
// backend's Pydantic models require. Deterministic, no fetch runtime.
describe("snakecaseJsonString", () => {
  it("converts a create payload to snake_case", () => {
    const out = snakecaseJsonString(
      JSON.stringify({
        parentFolderId: "1kl4",
        name: "RFC: Unify SIP",
        mimeType: "application/vnd.google-apps.document",
      }),
    );
    // snakecase-keys preserves key order, so the serialized output is
    // deterministic — compare strings to avoid a non-null assertion.
    expect(out).toBe(
      JSON.stringify({
        parent_folder_id: "1kl4",
        name: "RFC: Unify SIP",
        mime_type: "application/vnd.google-apps.document",
      }),
    );
  });

  it("converts a move payload to snake_case", () => {
    expect(snakecaseJsonString(JSON.stringify({ newParentFolderId: "abc" }))).toBe(
      JSON.stringify({ new_parent_folder_id: "abc" }),
    );
  });

  it("returns undefined for unparseable or non-object input", () => {
    expect(snakecaseJsonString("not json")).toBeUndefined();
    expect(snakecaseJsonString('"a bare string"')).toBeUndefined();
  });
});

// The Firefox regression guard: the hook must run for a JSON body
// without depending on the ``request.body`` getter (null in Firefox
// even when a body exists). It returning a Request — not ``undefined``
// — proves it didn't bail at the old ``if (!request.body)`` guard.
describe("snakecaseJsonBody", () => {
  it("transforms a JSON body (does not bail on the request.body getter)", async () => {
    const out = await snakecaseJsonBody({
      request: jsonRequest("http://api/files", { parentFolderId: "1kl4" }),
    });
    expect(out).toBeInstanceOf(Request);
  });

  it("leaves non-JSON requests untouched", async () => {
    const request = new Request("http://api/sidebar/drives", { method: "GET" });
    expect(await snakecaseJsonBody({ request })).toBeUndefined();
  });

  it("skips a JSON request with no body", async () => {
    const request = new Request("http://api/files", {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    expect(await snakecaseJsonBody({ request })).toBeUndefined();
  });
});

function jsonResponse(body: unknown): ResponsePromise {
  return Promise.resolve(
    new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } }),
  ) as ResponsePromise;
}

describe("validatedJson", () => {
  it("camelizes a valid snake_case payload", async () => {
    const out = await validatedJson(
      jsonResponse({ id: "u1", email: "a@sarj.ai", display_name: "A", avatar_url: null }),
      zUserProfile,
    );
    expect(out).toEqual({ id: "u1", email: "a@sarj.ai", displayName: "A", avatarUrl: null });
  });

  it("throws ApiValidationError on a mismatched payload outside production", async () => {
    await expect(
      validatedJson(jsonResponse([{ id: 123, name: "Eng" }]), z.array(zDriveSummary)),
    ).rejects.toThrow(ApiValidationError);
  });

  it("passes unknown extra keys through (returns raw data, not Zod's stripped parse)", async () => {
    const out = await validatedJson(
      jsonResponse([{ id: "d1", name: "Eng", brand_new_field: "kept" }]),
      z.array(zDriveSummary),
    );
    expect(out).toEqual([{ id: "d1", name: "Eng", brandNewField: "kept" }]);
  });
});

describe("rebuildForReplay", () => {
  it("rebuilds a stashed bodied request for the 401 replay", async () => {
    // The transformed request is what afterResponse hands the replay;
    // its stream is consumed by the first attempt, so the replay must be
    // a fresh Request built from the stash — never the same object.
    const transformed = await snakecaseJsonBody({
      request: jsonRequest("http://api/files", { parentFolderId: "1kl4" }),
    });
    if (!(transformed instanceof Request)) throw new Error("expected transform to yield a Request");
    const replay = rebuildForReplay(transformed);
    expect(replay).not.toBe(transformed);
    expect(replay.method).toBe("POST");
  });

  it("returns a bodyless request unchanged (no stash entry)", () => {
    const request = new Request("http://api/sidebar/drives", { method: "GET" });
    expect(rebuildForReplay(request)).toBe(request);
  });
});
