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

async function snakecaseJsonBody({
  request,
}: {
  request: Request;
}): Promise<Request | undefined> {
  if (!request.body) return undefined;
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) return undefined;
  const text = await request.clone().text();
  if (!text) return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return undefined;
  }
  if (!parsed || typeof parsed !== "object") return undefined;
  const transformed = snakecaseKeys(parsed as never, { deep: true });
  return new Request(request, { body: JSON.stringify(transformed) });
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
  // Use raw fetch for the replay so we don't recurse through this
  // same hook on the retried response.
  return fetch(request);
}

function throwOnError({ response }: { response: Response }): Response {
  if (!response.ok) {
    throw new ApiError(response.status, `API error: ${response.status}`);
  }
  return response;
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
