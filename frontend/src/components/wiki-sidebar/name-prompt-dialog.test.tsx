import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { NamePromptDialog } from "./name-prompt-dialog";

function submit(times = 1) {
  const form = document.querySelector("form");
  if (!form) throw new TypeError("Name prompt form missing");
  flushSync(() => {
    for (let index = 0; index < times; index++) {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
  });
}

describe("name prompt work ownership", () => {
  let root: Root;
  let host: HTMLDivElement;
  let frames: Map<number, FrameRequestCallback>;
  let nextFrame: number;
  const confirm = vi.fn<(value: string) => Promise<void> | void>();
  const change = vi.fn();

  beforeEach(() => {
    confirm.mockReset();
    change.mockClear();
    frames = new Map();
    nextFrame = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.set(++nextFrame, callback);
      return nextFrame;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  function render(open = true, defaultValue = "  First  ", title = "New document", strict = false) {
    const dialog = (
      <NamePromptDialog
        defaultValue={defaultValue}
        onConfirm={confirm}
        onOpenChange={change}
        open={open}
        title={title}
      />
    );
    flushSync(() => root.render(strict ? <StrictMode>{dialog}</StrictMode> : dialog));
  }

  it("admits only one confirmation when two submit events precede a render", async () => {
    const pending = Promise.withResolvers<undefined>();
    confirm.mockImplementation(async () => await pending.promise);
    render();
    submit(2);
    expect(confirm.mock.calls).toEqual([["First"]]);
    pending.resolve(undefined);
    await vi.waitFor(() => expect(change).toHaveBeenCalledWith(false));
  });

  it("does not close a replacement prompt when an earlier confirmation finishes", async () => {
    const pending = Promise.withResolvers<undefined>();
    confirm.mockImplementation(async () => await pending.promise);
    render();
    submit();
    render(false);
    render(true, "Second", "Rename document");
    change.mockClear();
    pending.resolve(undefined);
    await pending.promise;
    await Promise.resolve();
    await Promise.resolve();
    expect(change).not.toHaveBeenCalled();
    expect(document.querySelector("input")).toHaveValue("Second");
  });

  it("still closes the same open prompt when a successful mutation refreshes its default name", async () => {
    const pending = Promise.withResolvers<undefined>();
    confirm.mockImplementation(async () => await pending.promise);
    render();
    submit();
    render(true, "Refreshed name");
    pending.resolve(undefined);
    await vi.waitFor(() => expect(change).toHaveBeenCalledWith(false));
  });

  it("retires autofocus work and completion callbacks on unmount", async () => {
    const pending = Promise.withResolvers<undefined>();
    confirm.mockImplementation(async () => await pending.promise);
    render();
    submit();
    expect(frames.size).toBe(1);
    flushSync(() => root.unmount());
    expect(frames.size).toBe(0);
    pending.resolve(undefined);
    await pending.promise;
    await Promise.resolve();
    expect(change).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledOnce();
  });

  it("cancels autofocus when closed and still selects the current prompt on reopen", () => {
    render();
    expect(frames.size).toBe(1);
    render(false);
    expect(frames.size).toBe(0);
    render(true, "Second");
    expect(frames.size).toBe(1);
    const pending = frames.values().toArray();
    frames.clear();
    for (const callback of pending) callback(0);
    const input = document.querySelector("input");
    expect(document.activeElement).toBe(input);
    expect(input?.selectionStart).toBe(0);
    expect(input?.selectionEnd).toBe("Second".length);
  });

  it("keeps current autofocus and submission available after Strict Mode setup replay", async () => {
    const pending = Promise.withResolvers<undefined>();
    confirm.mockImplementation(async () => await pending.promise);
    render(true, "First", "New document", true);
    expect(frames.size).toBe(1);
    const callbacks = frames.values().toArray();
    frames.clear();
    for (const callback of callbacks) callback(0);
    expect(document.activeElement).toBe(document.querySelector("input"));
    submit(2);
    expect(confirm).toHaveBeenCalledOnce();
    pending.resolve(undefined);
    await vi.waitFor(() => expect(change).toHaveBeenCalledWith(false));
  });
});
