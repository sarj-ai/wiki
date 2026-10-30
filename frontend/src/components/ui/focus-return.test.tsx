import { useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { FocusReturnProvider } from "@/components/ui/focus-return";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

type Kind = "alert" | "dialog" | "sheet";
let setOpen: (open: boolean) => void;

function Overlay({
  externalTarget,
  kind = "dialog",
  nativeTrigger = false,
  onCloseAutoFocus,
  onOpenAutoFocus,
}: {
  externalTarget?: React.RefObject<HTMLElement | null>;
  kind?: Kind;
  nativeTrigger?: boolean;
  onCloseAutoFocus?: (event: Event) => void;
  onOpenAutoFocus?: (event: Event) => void;
}) {
  const [open, changeOpen] = useState(false);
  const targetRef = useRef<HTMLButtonElement | null>(null);
  useLayoutEffect(() => {
    setOpen = changeOpen;
  }, [changeOpen]);
  const Parts =
    kind === "sheet"
      ? {
          Root: Sheet,
          Content: SheetContent,
          Title: SheetTitle,
          Description: SheetDescription,
          Trigger: SheetTrigger,
        }
      : kind === "alert"
        ? {
            Root: AlertDialog,
            Content: AlertDialogContent,
            Title: AlertDialogTitle,
            Description: AlertDialogDescription,
            Trigger: AlertDialogTrigger,
          }
        : {
            Root: Dialog,
            Content: DialogContent,
            Title: DialogTitle,
            Description: DialogDescription,
            Trigger: DialogTrigger,
          };
  return (
    <FocusReturnProvider targetRef={externalTarget ?? targetRef}>
      <button onClick={() => changeOpen(true)} ref={targetRef} type="button">
        Owned opener
      </button>
      <Parts.Root onOpenChange={changeOpen} open={open}>
        {nativeTrigger ? <Parts.Trigger>Native opener</Parts.Trigger> : null}
        <Parts.Content onCloseAutoFocus={onCloseAutoFocus} onOpenAutoFocus={onOpenAutoFocus}>
          <Parts.Title>Owned overlay</Parts.Title>
          <Parts.Description>Focus ownership control.</Parts.Description>
          <input aria-label="First field" />
          <input aria-label="Consumer field" />
          {kind === "alert" ? (
            <AlertDialogCancel onClick={() => changeOpen(false)}>Cancel overlay</AlertDialogCancel>
          ) : (
            <button onClick={() => changeOpen(false)} type="button">
              Cancel overlay
            </button>
          )}
        </Parts.Content>
      </Parts.Root>
    </FocusReturnProvider>
  );
}

describe("native overlay focus return", () => {
  let host: HTMLDivElement;
  let root: Root;
  const externals: HTMLElement[] = [];

  beforeEach(() => {
    vi.useFakeTimers();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    flushSync(() => root.unmount());
    await vi.advanceTimersByTimeAsync(0);
    host.remove();
    for (const element of externals.splice(0)) element.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function render(props: React.ComponentProps<typeof Overlay> = {}) {
    flushSync(() => root.render(<Overlay {...props} />));
  }

  function outsideButton() {
    const element = document.createElement("button");
    element.textContent = "Outside owner";
    document.body.append(element);
    externals.push(element);
    return element;
  }

  it.each(["dialog", "sheet", "alert"] as const)(
    "returns controlled %s focus to its persistent owner",
    async (kind) => {
      render({ kind });
      const opener = await openOverlay();
      await close();
      expect(opener).toHaveFocus();
    },
  );

  it.each(["dialog", "sheet", "alert"] as const)(
    "preserves the native %s trigger ahead of an explicit fallback",
    async (kind) => {
      render({ kind, nativeTrigger: true });
      const opener = await openOverlay("Native opener");
      await close();
      expect(opener).toHaveFocus();
      expect(button("Owned opener")).not.toHaveFocus();
    },
  );

  it.each(["Escape", "backdrop"])(
    "returns controlled dialog focus after %s dismissal",
    async (dismissal) => {
      render();
      const opener = await openOverlay();
      flushSync(() => {
        if (dismissal === "Escape") {
          document
            .querySelector('[role="dialog"]')
            ?.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" }));
        } else {
          const overlay = document.querySelector('[data-slot="dialog-overlay"]');
          if (!(overlay instanceof HTMLElement)) throw new TypeError("Dialog backdrop missing");
          overlay.dispatchEvent(
            new PointerEvent("pointerdown", {
              bubbles: true,
              cancelable: true,
              pointerType: "mouse",
              button: 0,
            }),
          );
          overlay.dispatchEvent(
            new PointerEvent("pointerup", { bubbles: true, pointerType: "mouse", button: 0 }),
          );
          overlay.click();
        }
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(document.querySelector('[role="dialog"]')).toBeNull();
      expect(opener).toHaveFocus();
    },
  );

  it("composes consumer opening autofocus and honors close preventDefault", async () => {
    const outside = outsideButton();
    const onOpenAutoFocus = vi.fn((event: Event) => {
      event.preventDefault();
      document.querySelector<HTMLInputElement>('[aria-label="Consumer field"]')?.focus();
    });
    const onCloseAutoFocus = vi.fn((event: Event) => {
      event.preventDefault();
      outside.focus();
    });
    render({ nativeTrigger: true, onOpenAutoFocus, onCloseAutoFocus });
    flushSync(() => button("Native opener").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(onOpenAutoFocus).toHaveBeenCalledOnce();
    expect(document.querySelector('[aria-label="Consumer field"]')).toHaveFocus();
    await close();
    expect(onCloseAutoFocus).toHaveBeenCalledOnce();
    expect(outside).toHaveFocus();
  });

  it("leaves intentional consumer focus alone without a native trigger", async () => {
    const outside = outsideButton();
    render({ onCloseAutoFocus: () => outside.focus() });
    await openOverlay();
    await close();
    expect(outside).toHaveFocus();
  });

  it("fences a delayed old close while the controlled overlay reopens", async () => {
    const target = outsideButton();
    render({ externalTarget: { current: target } });
    await openOverlay();
    const restore = vi.spyOn(target, "focus");
    flushSync(() => setOpen(false));
    flushSync(() => setOpen(true));
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[aria-label="First field"]')).toHaveFocus();
    expect(restore).not.toHaveBeenCalled();
    await close();
    expect(target).toHaveFocus();
  });

  it("fences queued restoration when the provider retires even if its target survives", async () => {
    const target = outsideButton();
    render({ externalTarget: { current: target } });
    await openOverlay();
    const restore = vi.spyOn(target, "focus");
    flushSync(() => root.render(null));
    await vi.advanceTimersByTimeAsync(0);
    expect(target.isConnected).toBe(true);
    expect(restore).not.toHaveBeenCalled();
    expect(document.body).toHaveFocus();
  });

  it.each(["disconnected", "disabled", "hidden"])(
    "does not return focus to a %s target",
    async (state) => {
      const target = outsideButton();
      render({ externalTarget: { current: target } });
      await openOverlay();
      if (state === "disconnected") target.remove();
      else if (state === "disabled") target.disabled = true;
      else target.style.display = "none";
      const restore = vi.spyOn(target, "focus");
      await close();
      expect(restore).not.toHaveBeenCalled();
      expect(document.body).toHaveFocus();
    },
  );

  it("uses an explicitly supplied visible fallback when the original opener disappears", async () => {
    const original = outsideButton();
    const fallback = outsideButton();
    function WithFallback() {
      const [open, changeOpen] = useState(true);
      const originalRef = useRef(original);
      const fallbackRef = useRef(fallback);
      return (
        <FocusReturnProvider fallbackTargetRef={fallbackRef} targetRef={originalRef}>
          <Dialog onOpenChange={changeOpen} open={open}>
            <DialogContent>
              <DialogTitle>Fallback dialog</DialogTitle>
              <DialogDescription>Retired row opener.</DialogDescription>
              <button onClick={() => changeOpen(false)} type="button">
                Cancel fallback
              </button>
            </DialogContent>
          </Dialog>
        </FocusReturnProvider>
      );
    }
    flushSync(() => root.render(<WithFallback />));
    await vi.advanceTimersByTimeAsync(0);
    original.remove();
    flushSync(() => button("Cancel fallback").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(fallback).toHaveFocus();
  });

  it("returns a conditionally mounted controlled dialog to its surviving opener", async () => {
    flushSync(() => root.render(<Conditional />));
    const opener = button("Conditional opener");
    flushSync(() => {
      opener.focus();
      opener.click();
    });
    await vi.advanceTimersByTimeAsync(0);
    flushSync(() => button("Cancel conditional").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(opener).toHaveFocus();
  });

  it("returns sibling controlled dialogs to the parent opener before the outer owner", async () => {
    flushSync(() => root.render(<Siblings />));
    flushSync(() => {
      button("Parent opener").focus();
      button("Parent opener").click();
    });
    await vi.advanceTimersByTimeAsync(0);
    flushSync(() => {
      button("Choose destination").focus();
      button("Choose destination").click();
    });
    await vi.advanceTimersByTimeAsync(0);
    flushSync(() => button("Select My Drive").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(button("Choose destination")).toHaveFocus();
    flushSync(() => button("Cancel parent").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(button("Parent opener")).toHaveFocus();
  });

  it("resumes the parent Sheet's focus owner after an inner native dialog closes", async () => {
    flushSync(() => root.render(<Nested />));
    flushSync(() => button("Outer opener").click());
    await vi.advanceTimersByTimeAsync(0);
    flushSync(() => button("Inner opener").click());
    await vi.advanceTimersByTimeAsync(0);
    flushSync(() => button("Close inner").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(button("Inner opener")).toHaveFocus();
    flushSync(() => button("Close outer").click());
    await vi.advanceTimersByTimeAsync(0);
    expect(button("Outer opener")).toHaveFocus();
  });
});

function button(label: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find(
    (element) => element.textContent === label,
  );
  if (!(found instanceof HTMLButtonElement)) throw new TypeError(`Missing ${label}`);
  return found;
}

function Conditional() {
  const [open, changeOpen] = useState(false);
  return (
    <>
      <button onClick={() => changeOpen(true)} type="button">
        Conditional opener
      </button>
      {open ? (
        <Dialog onOpenChange={changeOpen} open>
          <DialogContent>
            <DialogTitle>Conditional dialog</DialogTitle>
            <DialogDescription>Normal conditional dismissal.</DialogDescription>
            <button onClick={() => changeOpen(false)} type="button">
              Cancel conditional
            </button>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
}

function Siblings() {
  const targetRef = useRef<HTMLButtonElement | null>(null);
  const [parent, setParent] = useState(false);
  const [child, setChild] = useState(false);
  return (
    <FocusReturnProvider targetRef={targetRef}>
      <button onClick={() => setParent(true)} ref={targetRef} type="button">
        Parent opener
      </button>
      <Dialog onOpenChange={setParent} open={parent}>
        <DialogContent>
          <DialogTitle>Parent dialog</DialogTitle>
          <DialogDescription>Sibling dialog ownership.</DialogDescription>
          <button onClick={() => setChild(true)} type="button">
            Choose destination
          </button>
          <button onClick={() => setParent(false)} type="button">
            Cancel parent
          </button>
        </DialogContent>
      </Dialog>
      {child ? (
        <Dialog onOpenChange={setChild} open>
          <DialogContent>
            <DialogTitle>Child destination</DialogTitle>
            <DialogDescription>Conditional child.</DialogDescription>
            <button onClick={() => setChild(false)} type="button">
              Select My Drive
            </button>
          </DialogContent>
        </Dialog>
      ) : null}
    </FocusReturnProvider>
  );
}

async function openOverlay(label = "Owned opener") {
  const opener = button(label);
  flushSync(() => {
    opener.focus();
    opener.click();
  });
  await vi.advanceTimersByTimeAsync(1);
  const initialFocus = document.querySelector('[role="alertdialog"]')
    ? button("Cancel overlay")
    : document.querySelector('[aria-label="First field"]');
  expect(initialFocus).toHaveFocus();
  return opener;
}

async function close() {
  flushSync(() => button("Cancel overlay").click());
  await vi.advanceTimersByTimeAsync(0);
}

function Nested() {
  const outerRef = useRef<HTMLButtonElement | null>(null);
  const [outer, setOuter] = useState(false);
  const [inner, setInner] = useState(false);
  return (
    <FocusReturnProvider targetRef={outerRef}>
      <button onClick={() => setOuter(true)} ref={outerRef} type="button">
        Outer opener
      </button>
      <Sheet onOpenChange={setOuter} open={outer}>
        <SheetContent>
          <SheetTitle>Outer sheet</SheetTitle>
          <SheetDescription>Nested focus owner.</SheetDescription>
          <Dialog onOpenChange={setInner} open={inner}>
            <DialogTrigger>Inner opener</DialogTrigger>
            <DialogContent>
              <DialogTitle>Inner dialog</DialogTitle>
              <DialogDescription>Nested dialog.</DialogDescription>
              <button onClick={() => setInner(false)} type="button">
                Close inner
              </button>
            </DialogContent>
          </Dialog>
          <button onClick={() => setOuter(false)} type="button">
            Close outer
          </button>
        </SheetContent>
      </Sheet>
    </FocusReturnProvider>
  );
}
