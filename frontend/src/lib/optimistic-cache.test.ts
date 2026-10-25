import { QueryClient } from "@tanstack/react-query";

import {
  activateMutationSession,
  captureMutationSession,
  retireMutationSession,
} from "./mutation-session";
import { updateOptimisticFields, updateOptimisticList } from "./optimistic-cache";

interface Item {
  id: string;
  value: string;
}

const KEY = ["fixture", "optimistic-list"];

describe("sparse optimistic entry ownership", () => {
  let client: QueryClient;

  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity } } });
    client.setQueryData<Item[]>(KEY, [
      { id: "a", value: "original-a" },
      { id: "b", value: "original-b" },
    ]);
  });

  afterEach(() => client.clear());

  function patch(id: string, value: string | undefined) {
    const change = updateOptimisticList<Item>(
      client,
      KEY,
      captureMutationSession(client),
      new Map([[id, () => (value === undefined ? undefined : { id, value })]]),
      (item) => item.id,
    );
    if (!change) throw new Error("Expected a loaded-list patch");
    return change;
  }

  it("rolls back only a denied entry while preserving an unrelated confirmed update", () => {
    const denied = patch("a", "denied-a");
    const confirmed = patch("b", "confirmed-b");
    expect(confirmed.settle()).toBe(false);
    denied.rollback();
    expect(denied.settle()).toBe(true);
    expect(client.getQueryData(KEY)).toEqual([
      { id: "a", value: "original-a" },
      { id: "b", value: "confirmed-b" },
    ]);
  });

  it.each(["older-first", "newer-first"])(
    "restores the original after two same-key failures: %s",
    (order) => {
      const first = patch("a", "first");
      const second = patch("a", "second");
      const changes = order === "older-first" ? [first, second] : [second, first];
      for (const change of changes) {
        change.rollback();
        change.settle();
      }
      expect(client.getQueryData<Item[]>(KEY)?.[0]?.value).toBe("original-a");
    },
  );

  it("leaves a newer same-key success untouched when the older operation fails late", () => {
    const first = patch("a", "first");
    const second = patch("a", "confirmed");
    second.settle();
    const current = client.getQueryData(KEY);
    first.rollback();
    first.settle();
    expect(client.getQueryData(KEY)).toBe(current);
    expect(client.getQueryData<Item[]>(KEY)?.[0]?.value).toBe("confirmed");
  });

  it("restores an earlier same-key success when the later operation fails", () => {
    const first = patch("a", "confirmed");
    const second = patch("a", "denied");
    first.settle();
    second.rollback();
    second.settle();
    expect(client.getQueryData<Item[]>(KEY)?.[0]?.value).toBe("confirmed");
  });

  it("preserves a fresh manual replacement and uses it as the next rollback baseline", () => {
    const old = patch("a", "old");
    client.setQueryData<Item[]>(KEY, [{ id: "a", value: "fresh" }]);
    const current = patch("a", "current");
    old.rollback();
    old.settle();
    current.rollback();
    current.settle();
    expect(client.getQueryData(KEY)).toEqual([{ id: "a", value: "fresh" }]);
  });

  it("abandons rollback after a fresh read, including structurally equal optimistic values", async () => {
    const old = patch("a", "equal-to-server");
    await client.query({
      queryKey: KEY,
      queryFn: () => [{ id: "a", value: "equal-to-server" }],
    });
    old.rollback();
    old.settle();
    expect(client.getQueryData(KEY)).toEqual([{ id: "a", value: "equal-to-server" }]);
  });

  it("accepts an equal external manual publication even when structural sharing retains its array", () => {
    const old = patch("a", "confirmed");
    client.setQueryData<Item[]>(KEY, [
      { id: "a", value: "confirmed" },
      { id: "b", value: "original-b" },
    ]);
    old.rollback();
    old.settle();
    expect(client.getQueryData<Item[]>(KEY)?.[0]?.value).toBe("confirmed");
  });

  it("restores an owned failure while preserving an unrelated external manual confirmation", () => {
    const old = patch("a", "denied");
    client.setQueryData<Item[]>(KEY, (data) =>
      data?.map((item) => (item.id === "b" ? { id: "b", value: "confirmed" } : item)),
    );
    old.rollback();
    old.settle();
    expect(client.getQueryData(KEY)).toEqual([
      { id: "a", value: "original-a" },
      { id: "b", value: "confirmed" },
    ]);
  });

  it("does not resurrect a removed entry after a fresh read confirmed its absence", async () => {
    const old = patch("a", undefined);
    await client.query({ queryKey: KEY, queryFn: () => [] });
    old.rollback();
    old.settle();
    expect(client.getQueryData(KEY)).toEqual([]);
  });

  it("never writes into a replacement query or a retired session", () => {
    const old = patch("a", "old");
    client.removeQueries({ exact: true, queryKey: KEY });
    client.setQueryData<Item[]>(KEY, [{ id: "a", value: "new-session" }]);
    old.rollback();
    old.settle();
    const retired = patch("a", "pending");
    retireMutationSession(client);
    client.clear();
    retired.rollback();
    expect(retired.settle()).toBe(false);
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });

  it("does not let a retired list finalizer delete the reactivated same-query owner", () => {
    const old = patch("a", "old");
    retireMutationSession(client);
    activateMutationSession(client);
    const first = patch("a", "current-a");
    old.settle();
    const second = patch("b", "current-b");
    expect(first.settle()).toBe(false);
    expect(second.settle()).toBe(true);
  });

  it("does not let a retired fields finalizer delete the reactivated same-query owner", () => {
    const key = ["fixture", "fields"];
    const original = { a: "old-a", b: "old-b" };
    client.setQueryData(key, original);
    const old = updateOptimisticFields(client, key, captureMutationSession(client), {
      a: "retired",
    });
    retireMutationSession(client);
    activateMutationSession(client);
    client.setQueryData(key, original);
    const first = updateOptimisticFields(client, key, captureMutationSession(client), {
      a: "pending-a",
    });
    old?.settle();
    const second = updateOptimisticFields(client, key, captureMutationSession(client), {
      b: "pending-b",
    });
    first?.confirm({ a: "confirmed-a" });
    first?.settle();
    expect(client.getQueryData(key)).toEqual({ a: "confirmed-a", b: "pending-b" });
    second?.confirm({ b: "confirmed-b" });
    second?.settle();
  });
});
