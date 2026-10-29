import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { FocusReturnProvider } from "@/components/ui/focus-return";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveSummary } from "@/types/wiki";

import { FolderPickerDialog } from "./folder-picker-dialog";

const READS = vi.hoisted(() => ({ drives: vi.fn(), folders: vi.fn() }));
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getDrives: READS.drives, getDriveFolders: READS.folders },
}));

function NestedDestination({ explicitOwner }: { explicitOwner: boolean }) {
  const opener = useRef<HTMLButtonElement | null>(null);
  const [parent, setParent] = useState(false);
  const [child, setChild] = useState(false);
  const [selection, setSelection] = useState("");
  const content = (
    <>
      <button onClick={() => setParent(true)} ref={opener} type="button">
        Polish opener
      </button>
      <Dialog onOpenChange={setParent} open={parent}>
        <DialogContent>
          <DialogTitle>Polish document</DialogTitle>
          <DialogDescription>Destination selection control.</DialogDescription>
          <button onClick={() => setChild(true)} type="button">
            Choose output destination
          </button>
          <output>{selection}</output>
          <button onClick={() => setParent(false)} type="button">
            Cancel polish
          </button>
        </DialogContent>
      </Dialog>
      {child ? (
        <FolderPickerDialog
          onOpenChange={setChild}
          onSelect={(destination) => setSelection(destination.driveName)}
          open
        />
      ) : null}
    </>
  );
  return explicitOwner ? (
    <FocusReturnProvider targetRef={opener}>{content}</FocusReturnProvider>
  ) : (
    content
  );
}

describe("native conditional destination-picker focus", () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    client.setQueryData<DriveSummary[]>(wikiKeys.drives, [
      { id: "my-drive", isPersonal: true, name: "My Drive" },
    ]);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    client.clear();
    await vi.advanceTimersByTimeAsync(0);
    host.remove();
    vi.useRealTimers();
  });

  it.each([false, true])(
    "returns selection to the surviving parent button with explicit owner=%s",
    async (explicitOwner) => {
      flushSync(() =>
        root.render(
          <StrictMode>
            <QueryClientProvider client={client}>
              <NestedDestination explicitOwner={explicitOwner} />
            </QueryClientProvider>
          </StrictMode>,
        ),
      );
      flushSync(() => {
        button("Polish opener").focus();
        button("Polish opener").click();
      });
      await vi.advanceTimersByTimeAsync(0);
      flushSync(() => {
        button("Choose output destination").focus();
        button("Choose output destination").click();
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2);
      flushSync(() => button("My Drive").click());
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
      expect(document.querySelector("output")).toHaveTextContent("My Drive");
      expect(button("Choose output destination")).toHaveFocus();
      expect(READS.drives).not.toHaveBeenCalled();
      expect(READS.folders).not.toHaveBeenCalled();
      flushSync(() => button("Cancel polish").click());
      await vi.advanceTimersByTimeAsync(0);
      expect(button("Polish opener")).toHaveFocus();
    },
  );
});

function button(text: string): HTMLButtonElement {
  const result = [...document.querySelectorAll("button")].find(
    (element) => element.textContent === text,
  );
  if (!(result instanceof HTMLButtonElement)) throw new TypeError(`Missing ${text}`);
  return result;
}
