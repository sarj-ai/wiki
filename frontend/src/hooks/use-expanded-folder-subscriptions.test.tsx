import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import type * as Zustand from "zustand";
import type { StateCreator } from "zustand";

import { BrowserUserContext } from "@/lib/browser-user-context";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

import { expandPath, resetExpansionState, useExpandedFolder } from "./use-expanded-folders";

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
  const create = (initialize?: StateCreator<unknown>) => {
    const bound = (stateCreator: StateCreator<unknown>) => actual.create(instrument(stateCreator));
    return initialize ? bound(initialize) : bound;
  };
  return { ...actual, create };
});

describe("folder expansion subscriptions", () => {
  let root: Root;
  let host: HTMLDivElement;
  let setFirst: (next: boolean) => void;

  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
    resetExpansionState("subscriptions-a");
    resetExpansionState("subscriptions-b");
    METRICS.active = 0;
    METRICS.notifications = 0;
    host = document.createElement("div");
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    resetExpansionState("subscriptions-a");
    resetExpansionState("subscriptions-b");
  });

  function Folder({ id }: { id: string }) {
    const [expanded, setExpanded] = useExpandedFolder(id);
    if (id === "folder-0") setFirst = setExpanded;
    return <span data-folder={id}>{expanded ? "open" : "closed"}</span>;
  }

  function render(user = "subscriptions-a") {
    flushSync(() =>
      root.render(
        <BrowserUserContext value={user}>
          {Array.from({ length: 500 }, (_, index) => (
            <Folder id={`folder-${index}`} key={index} />
          ))}
        </BrowserUserContext>,
      ),
    );
  }

  it("uses one live subscription per folder and one notification per observer on expansion", () => {
    render();
    flushSync(() => expandPath("subscriptions-a", "drive", ["folder-0"]));
    expect(METRICS).toEqual({ active: 500, notifications: 500 });
    expect(host.querySelector('[data-folder="folder-0"]')).toHaveTextContent("open");
    expect(host.querySelector('[data-folder="folder-1"]')).toHaveTextContent("closed");
    const notifications = METRICS.notifications;
    flushSync(() => setFirst(true));
    expect(METRICS.notifications).toBe(notifications);
    flushSync(() => setFirst(false));
    expect(host.querySelector('[data-folder="folder-0"]')).toHaveTextContent("closed");
    flushSync(() => root.unmount());
    expect(METRICS.active).toBe(0);
  });

  it("switches subscriptions and setters to the replacement user's expansion state", () => {
    render();
    flushSync(() => setFirst(true));
    render("subscriptions-b");
    expect(METRICS.active).toBe(500);
    expect(host.querySelector('[data-folder="folder-0"]')).toHaveTextContent("closed");
    flushSync(() => setFirst(true));
    expect(host.querySelector('[data-folder="folder-0"]')).toHaveTextContent("open");
    render();
    expect(host.querySelector('[data-folder="folder-0"]')).toHaveTextContent("open");
  });
});
