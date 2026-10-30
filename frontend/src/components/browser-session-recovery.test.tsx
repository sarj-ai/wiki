import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { StrictMode, useLayoutEffect } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { useCurrentUser } from "@/hooks/use-current-user";
import { resetExpansionState, useExpandedDrive } from "@/hooks/use-expanded-folders";
import type { User } from "@/lib/auth";
import {
  expandedDrivesStorageKey,
  readActiveBrowserUser,
  writeActiveBrowserUser,
} from "@/lib/browser-storage";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";
import { readVerifiedBrowserUser } from "@/lib/verified-browser-user";
import { authKeys } from "@/lib/wiki-keys";
import { WIKI_READ_API } from "@/lib/wiki-read-api";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

import { BrowserSessionBoundary } from "./browser-session-boundary";
import { QueryProvider } from "./query-provider";

vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getMe: vi.fn() } }));

const USER: User = {
  avatarUrl: undefined,
  displayName: "Recovered user",
  email: "recovered@sarj.ai",
  id: "recovered-user",
};
const CACHED_USER: User = { ...USER, displayName: "Cached other user", id: "cached-user" };

describe("fresh browser identity after anonymous server bootstrap", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;
  let pending: ReturnType<typeof Promise.withResolvers<User>>;

  function captureClient(currentClient: QueryClient) {
    client = currentClient;
  }

  function render({
    cachedUser,
    boundaryMounted = true,
    initialUserId = null,
    providerKey = "anonymous",
    strict = false,
  }: {
    cachedUser?: User;
    boundaryMounted?: boolean;
    initialUserId?: null | string;
    providerKey?: string;
    strict?: boolean;
  } = {}) {
    const workspace = (
      <QueryProvider key={providerKey} userId={initialUserId}>
        <ClientProbe onClient={captureClient} {...(cachedUser ? { cachedUser } : {})} />
        <AccountProbe />
        {boundaryMounted ? (
          <BrowserSessionBoundary initialUserId={initialUserId}>
            <WorkspaceProbe />
          </BrowserSessionBoundary>
        ) : null}
      </QueryProvider>
    );
    flushSync(() => root.render(strict ? <StrictMode>{workspace}</StrictMode> : workspace));
  }

  beforeEach(() => {
    installLocalStorage();
    localStorage.clear();
    sessionStorage.clear();
    resetExpansionState(USER.id);
    resetExpansionState(CACHED_USER.id);
    writeActiveBrowserUser(USER.id);
    localStorage.setItem(
      expandedDrivesStorageKey(USER.id),
      JSON.stringify({ state: { expanded: { "all-team": true }, initialized: true }, version: 1 }),
    );
    pending = Promise.withResolvers<User>();
    vi.mocked(WIKI_READ_API.getMe).mockReset().mockReturnValue(pending.promise);
    vi.spyOn(window.location, "replace").mockImplementation(() => null);
    host = document.createElement("div");
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    client.clear();
    resetExpansionState(USER.id);
    resetExpansionState(CACHED_USER.id);
    vi.restoreAllMocks();
  });

  it("restores the verified user's saved expansion after the account query recovers", async () => {
    render();
    expect(host).toHaveTextContent("identity:anonymous");
    expect(host).toHaveTextContent("my-drive:true;all-team:false");
    pending.resolve(USER);
    await vi.waitFor(() => expect(host).toHaveTextContent("account:Recovered user"));
    expect(host).toHaveTextContent("identity:recovered-user");
    expect(host).toHaveTextContent("my-drive:false;all-team:true");
    expect(WIKI_READ_API.getMe).toHaveBeenCalledOnce();
    expect(readActiveBrowserUser()).toBe(USER.id);
  });

  it("clears the recovered user's namespace on terminal authorization failure", async () => {
    render();
    pending.resolve(USER);
    await vi.waitFor(() => expect(host).toHaveTextContent("account:Recovered user"));
    expect(localStorage.getItem(expandedDrivesStorageKey(USER.id))).not.toBeNull();
    window.dispatchEvent(new Event(TERMINAL_UNAUTHORIZED_EVENT));
    expect(localStorage.getItem(expandedDrivesStorageKey(USER.id))).toBeNull();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    await vi.waitFor(() => expect(host).toHaveTextContent("identity:anonymous"));
  });

  it("does not adopt a cached other-user profile before a fresh response arrives", async () => {
    render({ cachedUser: CACHED_USER });
    await vi.waitFor(() => expect(host).toHaveTextContent("account:Cached other user"));
    expect(host).toHaveTextContent("identity:anonymous");
    expect(readActiveBrowserUser()).toBe(USER.id);
    expect(WIKI_READ_API.getMe).toHaveBeenCalledOnce();
    pending.resolve(USER);
    await vi.waitFor(() => expect(host).toHaveTextContent("identity:recovered-user"));
    expect(host).toHaveTextContent("my-drive:false;all-team:true");
  });

  it("does not recover a retired provider's identity after replacement", async () => {
    render();
    const retiredClient = client;
    const replacement = Promise.withResolvers<User>();
    vi.mocked(WIKI_READ_API.getMe).mockReturnValue(replacement.promise);
    render({ initialUserId: "replacement-user", providerKey: "replacement-user" });
    pending.resolve(USER);
    await vi.waitFor(() => expect(retiredClient.getQueryCache().getAll()).toHaveLength(0));
    expect(host).toHaveTextContent("identity:replacement-user");
    expect(readActiveBrowserUser()).toBe("replacement-user");
    expect(localStorage.getItem(expandedDrivesStorageKey(USER.id))).toBeNull();
    replacement.resolve({ ...USER, id: "replacement-user" });
  });

  it("does not publish a response after terminal session clear", async () => {
    render();
    window.dispatchEvent(new Event(TERMINAL_UNAUTHORIZED_EVENT));
    pending.resolve(USER);
    await vi.waitFor(() => expect(client.getQueryData(authKeys.me)).toBeUndefined());
    expect(host).toHaveTextContent("identity:anonymous");
  });

  it("does not adopt a replayed other-user cache entry after identity recovery", async () => {
    render();
    pending.resolve(USER);
    await vi.waitFor(() => expect(host).toHaveTextContent("identity:recovered-user"));
    client.setQueryData(authKeys.me, CACHED_USER);
    await vi.waitFor(() => expect(host).toHaveTextContent("account:Cached other user"));
    expect(host).toHaveTextContent("identity:anonymous");
    expect(readActiveBrowserUser()).toBe(USER.id);
  });

  it("preserves the server identity without an additional fresh request", async () => {
    render({ cachedUser: USER, initialUserId: USER.id, providerKey: USER.id });
    await vi.waitFor(() => expect(host).toHaveTextContent("identity:recovered-user"));
    expect(WIKI_READ_API.getMe).not.toHaveBeenCalled();
    expect(host).toHaveTextContent("my-drive:false;all-team:true");
  });

  it("releases an unmounted recovery waiter without aborting the remaining foreground account query", async () => {
    render();
    const signal = vi.mocked(WIKI_READ_API.getMe).mock.calls[0]?.[0];
    render({ boundaryMounted: false });
    expect(signal?.aborted).toBe(false);
    pending.resolve(USER);
    await vi.waitFor(() => expect(host).toHaveTextContent("account:Recovered user"));
    expect(WIKI_READ_API.getMe).toHaveBeenCalledOnce();
    expect(readVerifiedBrowserUser(client)).toBeNull();
  });

  it("recovers through Strict Mode while the abandoned read remains retired", async () => {
    const retired = Promise.withResolvers<User>();
    vi.mocked(WIKI_READ_API.getMe)
      .mockReturnValueOnce(retired.promise)
      .mockReturnValue(pending.promise);
    render({ strict: true });
    expect(WIKI_READ_API.getMe).toHaveBeenCalledTimes(2);
    expect(vi.mocked(WIKI_READ_API.getMe).mock.calls.map(([signal]) => signal?.aborted)).toEqual([
      true,
      false,
    ]);
    retired.resolve(CACHED_USER);
    pending.resolve(USER);
    await vi.waitFor(() => expect(host).toHaveTextContent("identity:recovered-user"));
    expect(host).toHaveTextContent("my-drive:false;all-team:true");
    expect(readActiveBrowserUser()).toBe(USER.id);
  });
});

function ClientProbe({
  cachedUser,
  onClient,
}: {
  cachedUser?: User;
  onClient: (client: QueryClient) => void;
}) {
  const client = useQueryClient();
  useLayoutEffect(() => {
    onClient(client);
    if (cachedUser) client.setQueryData(authKeys.me, cachedUser);
  }, [cachedUser, client, onClient]);
  return null;
}

function WorkspaceProbe() {
  const userId = useBrowserUserId();
  const [myDrive] = useExpandedDrive("my-drive", true);
  const [allTeam] = useExpandedDrive("all-team");
  return (
    <span>{`identity:${userId ?? "anonymous"};my-drive:${String(myDrive)};all-team:${String(allTeam)}`}</span>
  );
}

function AccountProbe() {
  const user = useCurrentUser();
  return <span>{`account:${user.data?.displayName ?? "pending"}`}</span>;
}
