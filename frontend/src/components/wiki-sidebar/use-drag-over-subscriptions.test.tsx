import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { renderToString } from "react-dom/server";
import type * as Zustand from "zustand";
import type { StateCreator } from "zustand";

import {
  useDragOverStore,
  useDropEdgeForRow,
  useIsCrossContainerDropTarget,
  useIsDropIntoFolder,
} from "./use-drag-over-store";

const METRICS = vi.hoisted(() => ({ active: 0, notifications: 0 }));

function instrument(initialize: StateCreator<unknown>): StateCreator<unknown> {
  return (set, get, api) => {
    const subscribe = api.subscribe;
    api.subscribe = (listener) => {
      METRICS.active++;
      const unsubscribe = subscribe((state, previous) => {
        METRICS.notifications++;
        listener(state, previous);
      });
      return () => {
        METRICS.active--;
        unsubscribe();
      };
    };
    return initialize(set, get, api);
  };
}

vi.mock("zustand", async (importOriginal) => {
  const actual = await importOriginal<typeof Zustand>();
  return {
    ...actual,
    create: (initialize: StateCreator<unknown>) => actual.create(instrument(initialize)),
  };
});

function Feedback({ id }: { id: string }) {
  const edge = useDropEdgeForRow(id);
  const into = useIsDropIntoFolder(id);
  const cross = useIsCrossContainerDropTarget(`parent:${id}`);
  return <span data-feedback={id}>{`${edge ?? "none"}:${into}:${cross}`}</span>;
}

describe("targeted drag feedback subscriptions", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    useDragOverStore.getState().reset();
    METRICS.active = 0;
    METRICS.notifications = 0;
    host = document.createElement("div");
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    useDragOverStore.getState().reset();
    vi.restoreAllMocks();
  });

  function render(ids: string[]) {
    flushSync(() => root.render(ids.map((id) => <Feedback id={id} key={id} />)));
  }

  it("uses one source subscription for a thousand rows and reads only affected snapshots", () => {
    render(Array.from({ length: 1_000 }, (_, index) => `row-${index}`));
    expect(METRICS.active).toBe(1);
    const state = useDragOverStore.getState();
    const owner = state.setActive({ containerId: "parent:origin", id: "dragged" });
    if (owner === null) throw new Error("Missing active drag");
    const reads = vi.spyOn(useDragOverStore, "getState");
    flushSync(() => {
      state.setOver({ containerId: "parent:row-0", id: "row-0", intent: "above" }, owner);
    });
    expect(reads.mock.calls.length).toBeLessThan(40);
    expect(host.querySelector('[data-feedback="row-0"]')).toHaveTextContent("above:true:true");
    expect(host.querySelector('[data-feedback="row-1"]')).toHaveTextContent("none:false:false");
    reads.mockClear();
    flushSync(() => {
      state.setOver({ containerId: "parent:row-999", id: "row-999", intent: "below" }, owner);
    });
    expect(reads.mock.calls.length).toBeLessThan(60);
    expect(host.querySelector('[data-feedback="row-0"]')).toHaveTextContent("none:false:false");
    expect(host.querySelector('[data-feedback="row-999"]')).toHaveTextContent("below:true:true");
    const notifications = METRICS.notifications;
    state.setOver({ containerId: "parent:row-999", id: "row-999", intent: "below" }, owner);
    expect(METRICS.notifications).toBe(notifications);
    flushSync(() => root.render(null));
    expect(METRICS.active).toBe(0);
  });

  it("preserves folder unions, edge changes and physical IDs with punctuation", () => {
    render(["", "a:physical/path", "b"]);
    const state = useDragOverStore.getState();
    state.setActive({ containerId: "parent:origin", id: "dragged" });
    flushSync(() => {
      state.setOver({ containerId: "parent:", id: "a:physical/path", intent: "into" });
    });
    expect(host.querySelector('[data-feedback=""]')).toHaveTextContent("none:true:true");
    expect(host.querySelector('[data-feedback="a:physical/path"]')).toHaveTextContent(
      "none:true:false",
    );
    flushSync(() => {
      state.setOver({ containerId: "parent:b", id: "a:physical/path", intent: "above" });
    });
    expect(host.querySelector('[data-feedback=""]')).toHaveTextContent("none:false:false");
    expect(host.querySelector('[data-feedback="a:physical/path"]')).toHaveTextContent(
      "above:false:false",
    );
    expect(host.querySelector('[data-feedback="b"]')).toHaveTextContent("none:true:true");
    flushSync(() => {
      state.setOver({ containerId: "parent:b", id: "a:physical/path", intent: "below" });
    });
    expect(host.querySelector('[data-feedback="a:physical/path"]')).toHaveTextContent(
      "below:false:false",
    );
  });

  it("retires owned feedback on abort and cannot revive it through old owners", () => {
    render(["old", "current"]);
    const state = useDragOverStore.getState();
    const old = new AbortController();
    const current = new AbortController();
    const oldOwner = state.setActive({ containerId: "parent:origin", id: "old-drag" }, old.signal);
    const owner = state.setActive({ containerId: "parent:origin", id: "new-drag" }, current.signal);
    if (oldOwner === null || owner === null) throw new Error("Missing drag owner");
    flushSync(() => {
      state.setOver({ containerId: "parent:current", id: "current", intent: "above" }, owner);
      state.setOver({ containerId: "parent:old", id: "old", intent: "into" }, oldOwner);
      state.reset(oldOwner);
      old.abort();
    });
    expect(host.querySelector('[data-feedback="current"]')).toHaveTextContent("above:true:true");
    expect(host.querySelector('[data-feedback="old"]')).toHaveTextContent("none:false:false");
    flushSync(() => current.abort());
    expect(host.textContent).toBe("none:false:falsenone:false:false");
    expect(
      state.setActive({ containerId: "parent:current", id: "retired" }, current.signal),
    ).toBeNull();
  });

  it("uses the empty server snapshot and resubscribes to current state after the last row retires", () => {
    const state = useDragOverStore.getState();
    state.setActive({ containerId: "parent:origin", id: "dragged" });
    state.setOver({ containerId: "parent:row", id: "row", intent: "below" });
    expect(renderToString(<Feedback id="row" />)).toContain("none:false:false");
    render(["row"]);
    expect(host.textContent).toBe("below:true:true");
    expect(METRICS.active).toBe(1);
    flushSync(() => root.render(null));
    expect(METRICS.active).toBe(0);
    state.reset();
    render(["row"]);
    expect(host.textContent).toBe("none:false:false");
    expect(METRICS.active).toBe(1);
  });
});
