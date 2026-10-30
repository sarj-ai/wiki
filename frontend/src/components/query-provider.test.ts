import { QueryClient } from "@tanstack/react-query";
import { dehydrate, hydrate } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

describe("hydrate ordering contract", () => {
  it("an older snapshot cannot clobber a newer cache entry", () => {
    const key = ["wiki", "drives"];

    const oldSession = new QueryClient();
    oldSession.setQueryData(key, [{ id: "stale" }], { updatedAt: 1_000 });
    const persistedSnapshot = dehydrate(oldSession);

    const client = new QueryClient();
    client.setQueryData(key, [{ id: "fresh-ssr" }], { updatedAt: 2_000 });

    hydrate(client, persistedSnapshot);
    expect(client.getQueryData(key)).toEqual([{ id: "fresh-ssr" }]);
  });

  it("a newer snapshot does overwrite an older entry", () => {
    const key = ["wiki", "drives"];

    const newerSession = new QueryClient();
    newerSession.setQueryData(key, [{ id: "newer" }], { updatedAt: 3_000 });
    const snapshot = dehydrate(newerSession);

    const client = new QueryClient();
    client.setQueryData(key, [{ id: "older" }], { updatedAt: 1_000 });

    hydrate(client, snapshot);
    expect(client.getQueryData(key)).toEqual([{ id: "newer" }]);
  });
});
