import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Suspense } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { retireDocumentAliasAction, saveDocumentAliasAction } from "@/app/actions/wiki-actions";
import { WIKI_READ_API } from "@/lib/wiki-read-api";

import { ShortLinkDialog } from "./short-link-dialog";
import type * as ShortLinkEditorModule from "./short-link-editor";

const EDITOR_CHUNK = vi.hoisted(() => ({
  admission: Promise.withResolvers<undefined>(),
  completion: Promise.withResolvers<undefined>(),
  started: false,
}));

// The App Router bundles this implementation; Node otherwise resolves the Pages Router version.
vi.mock("next/dynamic", async () => ({
  default: await vi.importActual("next/dist/shared/lib/app-dynamic"),
}));

vi.mock("./short-link-editor", async (importOriginal) => {
  EDITOR_CHUNK.started = true;
  await EDITOR_CHUNK.admission.promise;
  const editor = await importOriginal<typeof ShortLinkEditorModule>();
  EDITOR_CHUNK.completion.resolve(undefined);
  return editor;
});

vi.mock("@/lib/wiki-read-api", () => ({ WIKI_READ_API: { getDocumentAlias: vi.fn() } }));
vi.mock("@/app/actions/wiki-actions", () => ({
  retireDocumentAliasAction: vi.fn(),
  saveDocumentAliasAction: vi.fn(),
}));

describe("deferred short-link editor", () => {
  let root: Root;
  let host: HTMLDivElement;
  let client: QueryClient;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(WIKI_READ_API.getDocumentAlias)
      .mockReset()
      .mockResolvedValue({ alias: "team-handbook" });
    vi.mocked(saveDocumentAliasAction)
      .mockReset()
      .mockResolvedValue({
        data: { alias: "people-handbook", fileId: "file-a" },
        ok: true,
      });
    vi.mocked(retireDocumentAliasAction).mockReset().mockResolvedValue({ data: null, ok: true });
  });

  afterEach(() => {
    EDITOR_CHUNK.admission.resolve(undefined);
    flushSync(() => root.unmount());
    client.clear();
    host.remove();
  });

  function render(fileId = "file-a") {
    flushSync(() => {
      root.render(
        <QueryClientProvider client={client}>
          <Suspense fallback={<p role="status">Loading file</p>}>
            <iframe title="Settled document" />
            <ShortLinkDialog fileId={fileId} />
          </Suspense>
        </QueryClientProvider>,
      );
    });
  }

  it("keeps a settled viewer visible while the real editor import waits and retires a late file owner", async () => {
    render();
    const viewer = host.querySelector("iframe");
    const trigger = host.querySelector("button");
    if (!viewer || !trigger) throw new Error("Settled viewer or short-link trigger missing");
    flushSync(() => trigger.click());
    await vi.waitFor(() => expect(EDITOR_CHUNK.started).toBe(true));
    expect(viewer).toBeVisible();
    expect(host).not.toHaveTextContent("Loading file");
    expect(WIKI_READ_API.getDocumentAlias).not.toHaveBeenCalled();

    render("file-b");
    EDITOR_CHUNK.admission.resolve(undefined);
    await EDITOR_CHUNK.completion.promise;
    expect(host.querySelector("iframe")).toBe(viewer);
    expect(viewer).toBeVisible();
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    expect(WIKI_READ_API.getDocumentAlias).not.toHaveBeenCalled();
    expect(client.getQueryState(["document-alias", "file-b"])).toBeUndefined();
  });

  it("keeps the trigger named when its responsive text label is hidden", () => {
    render();
    const trigger = host.querySelector("button");
    if (!trigger) throw new Error("Short-link trigger missing");
    const label = trigger.querySelector("span");
    if (!label) throw new Error("Short-link text label missing");
    label.hidden = true;

    expect(trigger).toHaveAccessibleName("Short link");
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(WIKI_READ_API.getDocumentAlias).not.toHaveBeenCalled();
  });

  it("creates no alias query or dialog before the first click, then loads the current editor", async () => {
    render();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(WIKI_READ_API.getDocumentAlias).not.toHaveBeenCalled();
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    const trigger = host.querySelector("button");
    if (!trigger) throw new Error("Short-link trigger missing");
    flushSync(() => trigger.click());
    await vi.waitFor(() => {
      expect(document.body.querySelector<HTMLInputElement>("#document-alias")?.value).toBe(
        "team-handbook",
      );
    });
    expect(WIKI_READ_API.getDocumentAlias).toHaveBeenCalledOnce();
    const close = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent === "Close",
    );
    if (!close) throw new Error("Short-link close action missing");
    flushSync(() => close.click());
    await vi.waitFor(() => expect(document.body.querySelector('[role="dialog"]')).toBeNull());
  });

  it("closes an old editor on document navigation without querying the next alias", async () => {
    render();
    const trigger = host.querySelector("button");
    if (!trigger) throw new Error("Short-link trigger missing");
    flushSync(() => trigger.click());
    await vi.waitFor(() => expect(WIKI_READ_API.getDocumentAlias).toHaveBeenCalledOnce());
    render("file-b");
    expect(document.body.querySelector('[role="dialog"]')).toBeNull();
    expect(client.getQueryState(["document-alias", "file-b"])).toBeUndefined();
  });

  it("preserves alias editing and cache updates after loading the deferred editor", async () => {
    render();
    const trigger = host.querySelector("button");
    if (!trigger) throw new Error("Short-link trigger missing");
    flushSync(() => trigger.click());
    const input = await vi.waitFor(() => {
      const element = document.body.querySelector<HTMLInputElement>("#document-alias");
      if (!element) throw new Error("Alias editor input missing");
      return element;
    });
    flushSync(() => {
      const writeValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set?.bind(input);
      if (!writeValue) throw new Error("Input value setter missing");
      writeValue("people-handbook");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    flushSync(() => findButton("Save changes").click());
    await vi.waitFor(() => {
      expect(saveDocumentAliasAction).toHaveBeenCalledWith("file-a", "people-handbook");
      expect(client.getQueryData(["document-alias", "file-a"])).toEqual({
        alias: "people-handbook",
      });
    });
  });

  it("preserves explicit removal confirmation after loading the deferred editor", async () => {
    render();
    const trigger = host.querySelector("button");
    if (!trigger) throw new Error("Short-link trigger missing");
    flushSync(() => trigger.click());
    const remove = await vi.waitFor(() => findButton("Remove"));
    flushSync(() => remove.click());
    expect(retireDocumentAliasAction).not.toHaveBeenCalled();
    flushSync(() => findButton("Remove link").click());
    await vi.waitFor(() => {
      expect(retireDocumentAliasAction).toHaveBeenCalledWith("file-a");
      expect(client.getQueryData(["document-alias", "file-a"])).toEqual({ alias: undefined });
    });
  });
});

function findButton(label: string): HTMLButtonElement {
  const button = [...document.body.querySelectorAll<HTMLButtonElement>("button")].find(
    (element) => element.textContent === label,
  );
  if (!button) throw new Error(`Missing ${label} button`);
  return button;
}
