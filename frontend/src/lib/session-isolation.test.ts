// @vitest-environment happy-dom

import { QueryClient } from "@tanstack/react-query";

import { clearAuthenticatedSession } from "@/lib/session-isolation";

describe("authenticated session cleanup", () => {
  it("prevents a late in-flight query from repopulating cleared metadata", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { promise: upstream, resolve: resolveQuery } = Promise.withResolvers<{
      id: string;
      name: string;
    }>();
    const pending = queryClient.query({
      queryFn: async () => await upstream,
      queryKey: ["wiki", "file", "private-file"],
    });

    clearAuthenticatedSession(queryClient, null);
    resolveQuery({ id: "private-file", name: "Private roadmap" });
    await expect(pending).rejects.toThrow("CancelledError");
    await Promise.resolve();

    expect(queryClient.getQueryData(["wiki", "file", "private-file"])).toBeUndefined();
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
  });
});
