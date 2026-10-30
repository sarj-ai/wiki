import { QueryClient } from "@tanstack/react-query";

import {
  activateMutationSession,
  captureMutationSession,
  retireMutationSession,
} from "@/lib/mutation-session";

import { claimVerifiedBrowserUser, readVerifiedBrowserUser } from "./verified-browser-user";

it("keeps verified identities isolated between QueryClients", () => {
  const first = new QueryClient();
  const second = new QueryClient();
  expect(claimVerifiedBrowserUser(first, "user-a", captureMutationSession(first))).toBe(true);
  expect(readVerifiedBrowserUser(first)).toBe("user-a");
  expect(readVerifiedBrowserUser(second)).toBeNull();
});

it("rejects a conflicting identity within an active owner's lifetime", () => {
  const client = new QueryClient();
  const session = captureMutationSession(client);
  expect(claimVerifiedBrowserUser(client, "user-a", session)).toBe(true);
  expect(claimVerifiedBrowserUser(client, "user-b", session)).toBe(false);
  expect(readVerifiedBrowserUser(client)).toBe("user-a");
});

it("retires verified and pending identities before replacement epochs can claim one", () => {
  const client = new QueryClient();
  const retired = captureMutationSession(client);
  expect(claimVerifiedBrowserUser(client, "user-a", retired)).toBe(true);
  retireMutationSession(client);
  expect(readVerifiedBrowserUser(client)).toBeNull();
  expect(claimVerifiedBrowserUser(client, "user-a", retired)).toBe(false);
  activateMutationSession(client);
  expect(claimVerifiedBrowserUser(client, "user-a", retired)).toBe(false);
  expect(claimVerifiedBrowserUser(client, "user-b", captureMutationSession(client))).toBe(true);
  expect(readVerifiedBrowserUser(client)).toBe("user-b");
});
