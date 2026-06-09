import camelcaseKeys from "camelcase-keys";
import ky from "ky";
import snakecaseKeys from "snakecase-keys";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Single error type surfaced to query / mutation callers.
 *
 * The 401 redirect lives in the QueryClient's QueryCache / MutationCache
 * onError handlers (see ``query-provider.tsx``) — this module's job is
 * just to turn HTTP failures into typed exceptions and to refresh-and-
 * retry exactly once before giving up.
 */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Shared HTTP client.
 *
 * - ``prefixUrl`` so callers pass route-relative paths.
 * - Snakecases JSON request bodies in ``beforeRequest`` so app code can
 *   stay in camelCase end-to-end; FastAPI sees the snake_case shape it
 *   declares in its Pydantic models.
 * - ``parseJson`` deep-camelcases responses so callers consume the
 *   same camelCase shape as the rest of the codebase.
 * - On 401 (any path other than /auth/refresh itself), runs one
 *   deduplicated refresh attempt. If it succeeds we replay the request
 *   transparently; if it fails we throw ``ApiError(401)`` and let the
 *   QueryClient's onError handler navigate to /login.
 */
export const apiClient = ky.create({
  prefix: API_URL,
  credentials: "include",
  // We manage retry ourselves through the 401 hook; ky's automatic
  // retry doesn't carry the refresh round-trip and would also retry
  // mutations, which we don't want.
  retry: 0,
  parseJson: parseJsonCamelized,
  hooks: {
    beforeRequest: [snakecaseJsonBody],
    afterResponse: [refreshOn401, throwOnError],
  },
});

function parseJsonCamelized(text: string): unknown {
  if (!text) return undefined;
  const parsed: unknown = JSON.parse(text);
  if (parsed && typeof parsed === "object") {
    return camelcaseKeys(parsed as never, { deep: true });
  }
  return parsed;
}

// Snake_cased request bodies, keyed by the transformed Request, so a
// 401 replay can rebuild the body after the first attempt consumed the
// original stream (see ``rebuildForReplay``).
const replayBodyCache = new WeakMap<Request, string>();

// Exported for unit testing — wired into the client via the
// ``beforeRequest`` hook above.
export async function snakecaseJsonBody({
  request,
}: {
  request: Request;
}): Promise<Request | undefined> {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return undefined;
  // Read the body via ``clone().text()`` rather than gating on
  // ``request.body``. Firefox/Gecko returns ``null`` from the
  // ``Request.body`` getter even when a body is present, so the old
  // ``if (!request.body)`` guard short-circuited there — the transform
  // never ran and camelCase (``parentFolderId``/``mimeType``) shipped to
  // FastAPI, which 422s on its snake_case fields. ``.text()`` works in
  // every browser.
  const text = await request.clone().text();
  const body = text ? snakecaseJsonString(text) : undefined;
  if (body === undefined) return undefined;
  const snakecased = new Request(request, { body });
  replayBodyCache.set(snakecased, body);
  return snakecased;
}

// Pure transform: camelCase JSON text → snake_case JSON text. Returns
// undefined for unparseable input or a non-object payload so the caller
// leaves the request untouched. Split out from the Request plumbing so
// the conversion is unit-testable without a fetch runtime.
export function snakecaseJsonString(text: string): string | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object") return undefined;
  return JSON.stringify(snakecaseKeys(parsed as never, { deep: true }));
}

async function refreshOn401({
  request,
  response,
}: {
  request: Request;
  response: Response;
}): Promise<Response | undefined> {
  if (response.status !== 401) return undefined;
  if (request.url.endsWith("/auth/refresh")) return undefined;
  const refreshed = await deduplicatedRefresh();
  if (!refreshed) return undefined;
  // Use raw fetch for the replay so we don't recurse through this same
  // hook on the retried response. The first attempt already consumed the
  // request's body stream, so a bodied request must be rebuilt from the
  // stashed body — ``fetch(request)`` on a used Request throws
  // "already been used" and the retry silently fails.
  return fetch(rebuildForReplay(request));
}

export function rebuildForReplay(request: Request): Request {
  const body = replayBodyCache.get(request);
  // No stashed body → a bodyless request (GET/HEAD); replay as-is.
  if (body === undefined) return request;
  return new Request(request.url, {
    method: request.method,
    headers: request.headers,
    body,
    credentials: "include",
  });
}

async function throwOnError({ response }: { response: Response }): Promise<Response> {
  if (response.ok) return response;
  // Backend handlers (FilesError, FastAPI validation, default HTTPException)
  // all return a body shape with a `detail` field. Surface it as the error
  // message so toasts read like real explanations instead of generic
  // status codes. Fall back to the status if the body can't be parsed
  // (network blip, HTML error page from a proxy, etc.).
  const message = await readErrorMessage(response);
  throw new ApiError(response.status, message ?? `API error: ${response.status}`);
}

async function readErrorMessage(response: Response): Promise<string | null> {
  try {
    const text = await response.clone().text();
    if (!text) return null;
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object") return null;
    const detail = (parsed as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim().length > 0) return detail;
    // FastAPI validation errors return detail as an array of issues; pick
    // the first one's `msg` so the toast says something useful instead
    // of "[object Object]".
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown };
      if (typeof first.msg === "string") return first.msg;
    }
    return null;
  } catch {
    return null;
  }
}

let refreshPromise: Promise<boolean> | null = null;

function deduplicatedRefresh(): Promise<boolean> {
  refreshPromise ??= rawRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

// Direct fetch (not via apiClient) so we never recurse through the
// 401-refresh hook.
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
