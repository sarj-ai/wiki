import type { QueryClient, QueryKey } from "@tanstack/react-query";

interface Entry {
  failed: boolean;
  items: unknown[];
}

interface ItemOwner {
  entries: Entry[];
  expected: unknown[];
  original: unknown[];
  positions: number[];
}

interface ListOwner {
  active: number;
  data: undefined | unknown[];
  items: Map<string, ItemOwner>;
  release: () => void;
  writing: boolean;
}

export interface OptimisticListChange {
  rollback: () => void;
  settle: () => boolean;
}

const LIST_OWNERS = new WeakMap<object, ListOwner>();

interface FieldsOwner {
  base: object;
  pending: Map<symbol, object>;
  readRevision: number;
  release: () => void;
  writing: boolean;
}

export interface OptimisticFieldsChange<TData> {
  confirm: (data: TData) => void;
  rollback: () => void;
  settle: () => boolean;
}

const FIELDS_OWNERS = new WeakMap<object, FieldsOwner>();

// Stage reversible display settings; caller serializes their provider writes.
export function updateOptimisticFields<TData extends object>(
  qc: QueryClient,
  queryKey: QueryKey,
  session: AbortSignal,
  patch: Partial<TData>,
): OptimisticFieldsChange<TData> | undefined {
  const previous = qc.getQueryData<TData>(queryKey);
  const query = qc.getQueryCache().find({ exact: true, queryKey });
  if (!previous || !query || session.aborted) return undefined;
  const owner = fieldsOwner(qc, query, queryKey, session, previous);
  const id = Symbol();
  const readRevision = owner.readRevision;
  owner.pending.set(id, patch);
  writePendingFields(qc, queryKey, owner);
  const restore = (confirmed?: TData) => {
    if (session.aborted || qc.getQueryCache().find({ exact: true, queryKey }) !== query) return;
    if (confirmed && owner.readRevision === readRevision)
      owner.base = { ...owner.base, ...confirmed };
    owner.pending.delete(id);
    writePendingFields(qc, queryKey, owner);
  };
  return {
    confirm: restore,
    rollback: () => restore(),
    settle: () => {
      if (owner.pending.size > 0) return false;
      owner.release();
      if (FIELDS_OWNERS.get(query) === owner) FIELDS_OWNERS.delete(query);
      return (
        owner.readRevision > 0 &&
        !session.aborted &&
        qc.getQueryCache().find({ exact: true, queryKey }) === query
      );
    },
  };
}

function fieldsOwner(
  qc: QueryClient,
  query: object,
  queryKey: QueryKey,
  session: AbortSignal,
  previous: object,
): FieldsOwner {
  const existing = FIELDS_OWNERS.get(query);
  if (existing) return existing;
  const owner: FieldsOwner = {
    base: previous,
    pending: new Map(),
    readRevision: 0,
    release: () => {
      releaseCache();
      session.removeEventListener("abort", onAbort);
    },
    writing: false,
  };
  const releaseCache = qc.getQueryCache().subscribe((event) => {
    if (event.query !== query || owner.writing) return;
    if (event.type === "removed") owner.release();
    if (event.type !== "updated" || event.action.type !== "success") return;
    const data = qc.getQueryData<object>(queryKey);
    if (!data) return;
    owner.base = data;
    owner.readRevision += 1;
    writePendingFields(qc, queryKey, owner);
  });
  const onAbort = () => {
    owner.pending.clear();
    owner.release();
    if (FIELDS_OWNERS.get(query) === owner) FIELDS_OWNERS.delete(query);
  };
  session.addEventListener("abort", onAbort, { once: true });
  FIELDS_OWNERS.set(query, owner);
  return owner;
}

function writePendingFields(qc: QueryClient, queryKey: QueryKey, owner: FieldsOwner): void {
  let next = owner.base;
  for (const patch of owner.pending.values()) next = { ...next, ...patch };
  owner.writing = true;
  try {
    qc.setQueryData(queryKey, next);
  } finally {
    owner.writing = false;
  }
}

// Own only changed entries, rather than a complete array snapshot.
export function updateOptimisticList<TItem>(
  qc: QueryClient,
  queryKey: QueryKey,
  session: AbortSignal,
  updates: ReadonlyMap<string, ((existing: TItem | undefined) => TItem | undefined) | undefined>,
  itemId: (item: TItem) => string,
  append = false,
): OptimisticListChange | undefined {
  const previous = qc.getQueryData<TItem[]>(queryKey);
  const query = qc.getQueryCache().find({ exact: true, queryKey });
  if (!previous || !query || session.aborted || updates.size === 0) return undefined;
  const owner = listOwner(qc, query, queryKey, session, itemId, previous);
  const { next, originals, positions, values } = buildListUpdate(previous, updates, itemId, append);
  owner.writing = true;
  const current = qc.setQueryData<TItem[]>(queryKey, next);
  owner.writing = false;
  owner.data = current;
  const entries = new Map<string, Entry>();
  for (const [id, item] of values) {
    const original = originals.get(id);
    let chain = owner.items.get(id);
    if (chain && !sameItems(original?.items ?? [], chain.expected)) chain = undefined;
    if (!chain) {
      chain = {
        entries: [],
        expected: [],
        original: original?.items ?? [],
        positions: original?.positions ?? [previous.length],
      };
      owner.items.set(id, chain);
    }
    const position = positions.get(id);
    const written = position === undefined ? undefined : current?.[position];
    const entry: Entry = { failed: false, items: item === undefined ? [] : [item] };
    chain.entries.push(entry);
    chain.expected = written === undefined ? [] : [written];
    entries.set(id, entry);
  }
  owner.active += 1;
  let settled = false;
  return {
    rollback: () => {
      if (
        settled ||
        session.aborted ||
        qc.getQueryCache().find({ exact: true, queryKey }) !== query
      )
        return;
      for (const entry of entries.values()) entry.failed = true;
      const data = qc.getQueryData<TItem[]>(queryKey);
      if (!data) return;
      const restored = restoreOwnedItems(data, owner, entries, itemId);
      if (restored === data) return;
      owner.writing = true;
      qc.setQueryData<unknown[]>(queryKey, restored);
      owner.writing = false;
      owner.data = qc.getQueryData<TItem[]>(queryKey);
      recordExpectedItems(qc.getQueryData<TItem[]>(queryKey), owner, entries, itemId);
    },
    settle: () => {
      if (settled) return false;
      settled = true;
      owner.active -= 1;
      if (owner.active !== 0) return false;
      owner.release();
      if (LIST_OWNERS.get(query) === owner) LIST_OWNERS.delete(query);
      return !session.aborted && qc.getQueryCache().find({ exact: true, queryKey }) === query;
    },
  };
}

function buildListUpdate<TItem>(
  previous: TItem[],
  updates: ReadonlyMap<string, ((existing: TItem | undefined) => TItem | undefined) | undefined>,
  itemId: (item: TItem) => string,
  append: boolean,
) {
  const originals = new Map<string, { items: TItem[]; positions: number[] }>();
  const values = new Map<string, TItem | undefined>();
  const positions = new Map<string, number>();
  const next: TItem[] = [];
  for (const [index, item] of previous.entries()) {
    const id = itemId(item);
    const build = updates.get(id);
    if (!updates.has(id)) {
      next.push(item);
      continue;
    }
    let original = originals.get(id);
    if (!original) {
      original = { items: [], positions: [] };
      originals.set(id, original);
      const value = build?.(item);
      values.set(id, value);
      if (!append) appendUpdate(next, positions, id, value);
    }
    original.items.push(item);
    original.positions.push(index);
  }
  for (const [id, build] of updates) {
    if (!originals.has(id)) values.set(id, build?.(undefined));
    if (append || !originals.has(id)) appendUpdate(next, positions, id, values.get(id));
  }
  return { next, originals, positions, values };
}

function appendUpdate<TItem>(
  next: TItem[],
  positions: Map<string, number>,
  id: string,
  item: TItem | undefined,
): void {
  if (item === undefined) return;
  positions.set(id, next.length);
  next.push(item);
}

function listOwner<TItem>(
  qc: QueryClient,
  query: object,
  queryKey: QueryKey,
  session: AbortSignal,
  itemId: (item: TItem) => string,
  previous: TItem[],
): ListOwner {
  // Each query key is used with one immutable element type for its lifetime.
  const existing = LIST_OWNERS.get(query);
  if (existing) return existing;
  const owner: ListOwner = {
    active: 0,
    data: previous,
    items: new Map(),
    release: () => {
      releaseCache();
      session.removeEventListener("abort", onAbort);
    },
    writing: false,
  };
  const releaseCache = qc.getQueryCache().subscribe((event) => {
    if (event.query !== query || owner.writing) return;
    // A fresh read or eviction supersedes local rollback authority, even if
    // structural sharing retains an equal optimistic value or absent entry.
    if (
      event.type === "removed" ||
      (event.type === "updated" && event.action.type === "success" && !event.action.manual)
    ) {
      owner.items.clear();
    } else if (event.type === "updated" && event.action.type === "success") {
      const data = qc.getQueryData<TItem[]>(queryKey);
      // An equal whole-array external publication has no per-item provenance;
      // conservatively accept it as an authoritative confirmation.
      if (data === owner.data) owner.items.clear();
      else discardReplacedItems(data, owner, itemId);
      owner.data = data;
    }
  });
  const onAbort = () => {
    owner.items.clear();
    owner.release();
    if (LIST_OWNERS.get(query) === owner) LIST_OWNERS.delete(query);
  };
  session.addEventListener("abort", onAbort, { once: true });
  LIST_OWNERS.set(query, owner);
  return owner;
}

function discardReplacedItems<TItem>(
  data: TItem[] | undefined,
  owner: ListOwner,
  itemId: (item: TItem) => string,
): void {
  const current = new Map<string, TItem[]>();
  if (!data) {
    owner.items.clear();
    return;
  }
  for (const item of data) {
    const id = itemId(item);
    if (!owner.items.has(id)) continue;
    const group = current.get(id) ?? [];
    group.push(item);
    current.set(id, group);
  }
  for (const [id, chain] of owner.items) {
    if (!sameItems(current.get(id) ?? [], chain.expected)) owner.items.delete(id);
  }
}

function restoreOwnedItems<TItem>(
  data: TItem[],
  owner: ListOwner,
  entries: Map<string, Entry>,
  itemId: (item: TItem) => string,
): unknown[] {
  const current = new Map<string, TItem[]>();
  for (const item of data) {
    const id = itemId(item);
    if (!entries.has(id)) continue;
    const group = current.get(id) ?? [];
    group.push(item);
    current.set(id, group);
  }
  const replacements = new Map<string, ItemOwner>();
  for (const id of entries.keys()) {
    const chain = owner.items.get(id);
    const group = current.get(id) ?? [];
    if (!chain || !sameItems(group, chain.expected)) {
      owner.items.delete(id);
      continue;
    }
    const failed = entries.get(id);
    if (!failed) continue;
    const latest = chain.entries.findLast((entry) => !entry.failed);
    if (latest && chain.entries.indexOf(latest) > chain.entries.indexOf(failed)) continue;
    replacements.set(id, chain);
  }
  if (replacements.size === 0) return data;
  const next: unknown[] = data.filter((item) => !replacements.has(itemId(item)));
  for (const chain of replacements.values()) {
    const replacement = chain.entries.findLast((entry) => !entry.failed)?.items ?? chain.original;
    for (const [index, item] of replacement.entries()) {
      const position = chain.positions[index] ?? chain.positions[0] ?? next.length;
      next.splice(Math.min(position, next.length), 0, item);
    }
  }
  return next;
}

function recordExpectedItems<TItem>(
  data: TItem[] | undefined,
  owner: ListOwner,
  entries: Map<string, Entry>,
  itemId: (item: TItem) => string,
): void {
  for (const id of entries.keys()) {
    const chain = owner.items.get(id);
    if (chain) chain.expected = [];
  }
  if (!data) return;
  for (const item of data) {
    const id = itemId(item);
    if (entries.has(id)) owner.items.get(id)?.expected.push(item);
  }
}

function sameItems(left: unknown[], right: unknown[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}
