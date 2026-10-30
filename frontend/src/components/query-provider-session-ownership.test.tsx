import { type QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { StrictMode, useLayoutEffect } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { z } from "zod";

import { runAction } from "@/lib/action-client";
import { ApiError } from "@/lib/api-error";
import { wikiReadHttp } from "@/lib/clients/wiki-read-http";
import { TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";
import { broadcastSessionClear, clearAuthenticatedSession } from "@/lib/session-isolation";
import { makeWikiApi } from "@/lib/wiki-api";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

import { QueryProvider } from "./query-provider";

vi.mock("@/lib/session-isolation", () => ({
  broadcastSessionClear: vi.fn(),
  clearAuthenticatedSession: vi.fn(),
  subscribeToSessionClear: vi.fn(() => vi.fn()),
}));

describe("query provider session ownership", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;
  const clients = new Set<QueryClient>();
  const terminalUnauthorized = vi.fn<() => void>();
  const replace = vi.fn<Location["replace"]>();

  function captureClient(currentClient: QueryClient) {
    client = currentClient;
    clients.add(currentClient);
  }

  function renderProvider(userId: string, key = userId, children?: React.ReactNode) {
    flushSync(() =>
      root.render(
        <QueryProvider key={key} userId={userId}>
          <CaptureClient onClient={captureClient} />
          {children}
        </QueryProvider>,
      ),
    );
  }

  beforeEach(() => {
    vi.useFakeTimers();
    installLocalStorage();
    vi.spyOn(document, "cookie", "get").mockReturnValue("");
    replace.mockClear();
    vi.spyOn(window.location, "replace").mockImplementation(replace);
    vi.mocked(clearAuthenticatedSession).mockClear();
    vi.mocked(broadcastSessionClear).mockClear();
    terminalUnauthorized.mockClear();
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, terminalUnauthorized);
    host = document.createElement("div");
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    await vi.advanceTimersByTimeAsync(0);
    for (const currentClient of clients) currentClient.clear();
    clients.clear();
    window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, terminalUnauthorized);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("aborts only the retired user's reads and keeps the replacement cache and storage", async () => {
    renderProvider("user-a");
    const retiredClient = client;
    const pending = Promise.withResolvers<string>();
    let signal: AbortSignal | undefined;
    const retiredRead = retiredClient
      .query({
        queryFn: async (context) => {
          signal = context.signal;
          return await pending.promise;
        },
        queryKey: ["private-document"],
      })
      .catch(() => null);

    renderProvider("user-b");
    localStorage.setItem("replacement-user-storage", "keep");
    client.setQueryData(["private-document"], "replacement-data");
    expect(signal?.aborted).toBe(true);
    pending.reject(new ApiError(401, "Retired session expired"));
    await retiredRead;
    await vi.advanceTimersByTimeAsync(0);

    expect(retiredClient.getQueryCache().getAll()).toHaveLength(0);
    expect(client.getQueryData(["private-document"])).toBe("replacement-data");
    expect(localStorage.getItem("replacement-user-storage")).toBe("keep");
    expect(terminalUnauthorized).not.toHaveBeenCalled();
    expect(clearAuthenticatedSession).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("ignores a late 401 from the retired mutation cache", async () => {
    renderProvider("user-a");
    const pending = Promise.withResolvers<string>();
    const mutation = client.getMutationCache().build(client, {
      gcTime: Infinity,
      mutationFn: async () => await pending.promise,
    });
    const retiredMutation = mutation.execute(undefined).catch(() => null);
    await Promise.resolve();

    renderProvider("user-b");
    pending.reject(new ApiError(401, "Retired mutation session expired"));
    await retiredMutation;
    await vi.advanceTimersByTimeAsync(0);

    expect(terminalUnauthorized).not.toHaveBeenCalled();
    expect(clearAuthenticatedSession).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it.each(["user-b", "user-a"])(
    "prevents an unsignalled retired action from refreshing the replacement %s provider",
    async (replacementUser) => {
      renderProvider("user-a", "old-provider");
      const pending = Promise.withResolvers<{ error: string; ok: false; status: number }>();
      const action = vi.fn(async () => await pending.promise);
      const fetch = vi.fn<typeof globalThis.fetch>();
      vi.stubGlobal("fetch", fetch);
      const mutation = client.getMutationCache().build(client, {
        gcTime: Infinity,
        mutationFn: async () => await runAction(action),
      });
      const retiredMutation = mutation.execute(undefined).catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(0);
      expect(action).toHaveBeenCalledOnce();

      renderProvider(replacementUser, "new-provider");
      client.setQueryData(["replacement-document"], "keep");
      pending.resolve({ error: "Retired action expired", ok: false, status: 401 });
      await expect(retiredMutation).resolves.toMatchObject({ name: "AbortError" });
      expect(action).toHaveBeenCalledOnce();
      expect(fetch).not.toHaveBeenCalled();
      expect(terminalUnauthorized).not.toHaveBeenCalled();
      expect(clearAuthenticatedSession).not.toHaveBeenCalled();
      expect(client.getQueryData(["replacement-document"])).toBe("keep");
    },
  );

  it("prevents a dispatched generated mutation from recovering into a replacement provider", async () => {
    renderProvider("user-a");
    const pending = Promise.withResolvers<Response>();
    const fetch = vi.fn<typeof globalThis.fetch>().mockReturnValue(pending.promise);
    vi.stubGlobal("fetch", fetch);
    const operation = makeWikiApi().renameFile("file-1", "Old draft");
    const outcome = operation.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledOnce();
    const request = fetch.mock.calls[0]?.[0];
    if (!(request instanceof Request))
      throw new TypeError("Expected the generated mutation request");

    renderProvider("user-b");
    expect(request.signal.aborted).toBe(false);
    pending.resolve(Response.json({ detail: "Retired session expired" }, { status: 401 }));
    await expect(outcome).resolves.toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledOnce();
    expect(terminalUnauthorized).not.toHaveBeenCalled();
    expect(clearAuthenticatedSession).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("stops a retired read transport from publishing its own terminal auth event", async () => {
    const pending = Promise.withResolvers<Response>();
    const fetch = vi.fn<typeof globalThis.fetch>().mockReturnValue(pending.promise);
    vi.stubGlobal("fetch", fetch);
    renderProvider("user-a");
    const retiredRead = client
      .query({
        queryFn: async ({ signal }) =>
          await wikiReadHttp("/files/old-user", z.object({ value: z.string() }), {}, signal),
        queryKey: ["private-http-document"],
      })
      .catch(() => null);
    expect(fetch).toHaveBeenCalledOnce();

    renderProvider("user-b");
    pending.resolve(new Response(null, { status: 401 }));
    await retiredRead;
    await vi.advanceTimersByTimeAsync(0);

    expect(fetch).toHaveBeenCalledOnce();
    expect(terminalUnauthorized).not.toHaveBeenCalled();
    expect(clearAuthenticatedSession).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("retains hydrated data and resumes observed reads through Strict Mode cleanup", async () => {
    const pending = Promise.withResolvers<string>();
    const unneededRefetch = vi.fn<() => Promise<string>>().mockResolvedValue("unneeded-refetch");
    const signals: AbortSignal[] = [];
    const read = vi.fn(async ({ signal }: { signal: AbortSignal }) => {
      signals.push(signal);
      return await pending.promise;
    });
    function ReadProbe() {
      const hydrated = useQuery({
        initialData: "hydrated",
        queryFn: unneededRefetch,
        queryKey: ["hydrated-document"],
      });
      const observed = useQuery({ queryFn: read, queryKey: ["observed-document"] });
      return <span>{`${hydrated.data}:${observed.data ?? "pending"}`}</span>;
    }
    flushSync(() =>
      root.render(
        <StrictMode>
          <QueryProvider userId="user-a">
            <CaptureClient onClient={captureClient} />
            <ReadProbe />
          </QueryProvider>
        </StrictMode>,
      ),
    );
    pending.resolve("loaded");
    await vi.advanceTimersByTimeAsync(0);

    expect(read).toHaveBeenCalledTimes(2);
    expect(unneededRefetch).not.toHaveBeenCalled();
    expect(signals.map((signal) => signal.aborted)).toEqual([true, false]);
    expect(client.getQueryData(["hydrated-document"])).toBe("hydrated");
    expect(client.getQueryData(["observed-document"])).toBe("loaded");
    expect(host.textContent).toBe("hydrated:loaded");
  });

  it("adopts child HTTP reads through initial and Strict Mode provider effect setup", async () => {
    const retiredResponse = Promise.withResolvers<Response>();
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockReturnValueOnce(retiredResponse.promise)
      .mockResolvedValueOnce(Response.json({ value: "loaded" }));
    vi.stubGlobal("fetch", fetch);
    flushSync(() =>
      root.render(
        <StrictMode>
          <QueryProvider userId="user-a">
            <CaptureClient onClient={captureClient} />
            <HttpReadProbe />
          </QueryProvider>
        </StrictMode>,
      ),
    );
    retiredResponse.resolve(new Response(null, { status: 401 }));
    await vi.advanceTimersByTimeAsync(0);

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls.map(([, init]) => init?.signal?.aborted)).toEqual([true, false]);
    expect(client.getQueryData(["strict-http-document"])).toEqual({ value: "loaded" });
    expect(host.textContent).toBe("loaded");
    expect(terminalUnauthorized).not.toHaveBeenCalled();
  });

  it("lets a replacement provider for the same user finish its own request", async () => {
    const pending = Promise.withResolvers<string>();
    const signals: AbortSignal[] = [];
    const read = vi.fn(async ({ signal }: { signal: AbortSignal }) => {
      signals.push(signal);
      return await (signals.length === 1 ? pending.promise : Promise.resolve("replacement"));
    });
    function ReadProbe() {
      const observed = useQuery({ queryFn: read, queryKey: ["observed-document"] });
      return <span>{observed.data ?? "pending"}</span>;
    }
    renderProvider("user-a", "initial-provider", <ReadProbe />);
    renderProvider("user-a", "replacement-provider", <ReadProbe />);
    pending.reject(new ApiError(401, "Retired same-user request expired"));
    await vi.advanceTimersByTimeAsync(0);

    expect(read).toHaveBeenCalledTimes(2);
    expect(signals.map((signal) => signal.aborted)).toEqual([true, false]);
    expect(client.getQueryData(["observed-document"])).toBe("replacement");
    expect(host.textContent).toBe("replacement");
    expect(terminalUnauthorized).not.toHaveBeenCalled();
    expect(clearAuthenticatedSession).not.toHaveBeenCalled();
  });

  it("keeps terminal auth handling for the current provider", async () => {
    renderProvider("user-a");
    const deniedRead = vi
      .fn<() => Promise<string>>()
      .mockRejectedValue(new ApiError(401, "Current session expired"));
    await client
      .query({
        queryFn: deniedRead,
        queryKey: ["current-document"],
      })
      .catch(() => null);

    expect(terminalUnauthorized).toHaveBeenCalledOnce();
    expect(clearAuthenticatedSession).toHaveBeenCalledWith(client, "user-a");
    expect(broadcastSessionClear).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledOnce();
  });
});

function CaptureClient({ onClient }: { onClient: (client: QueryClient) => void }) {
  const client = useQueryClient();
  useLayoutEffect(() => {
    onClient(client);
  }, [client, onClient]);
  return null;
}

function HttpReadProbe() {
  const observed = useQuery({
    queryFn: async ({ signal }) =>
      await wikiReadHttp("/files/current-user", z.object({ value: z.string() }), {}, signal),
    queryKey: ["strict-http-document"],
  });
  return <span>{observed.data?.value ?? "pending"}</span>;
}
