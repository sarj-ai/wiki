import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { DOCUMENT_TRANSFORM_API } from "@/lib/document-transform-api";
import type { TransformRecovery } from "@/lib/document-transform-recovery";
import {
  makeMarkdownRecovery,
  markdownRecoveryStorageKey,
  saveMarkdownRecovery,
} from "@/lib/markdown-transform-recovery";

import { DocumentPolishRecovery } from "./document-polish-recovery";

const Operation: TransformRecovery = {
  createdAt: Date.now(),
  key: "55555555-5555-4555-8555-555555555555",
  mode: "unslop",
  outputStrategy: "copy",
  source: null,
  taskId: null,
};
const Source = {
  kind: "markdown",
  content: "# Exact draft\n\nPrivate 😀",
  name: "Exact name",
  destinationFolderId: "folder",
} as const;
let root: Root;
let host: HTMLDivElement;
const Restore = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  flushSync(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

function renderPanel(source: null | typeof Source = Source) {
  flushSync(() =>
    root.render(
      <DocumentPolishRecovery
        onRestore={Restore}
        operation={Operation}
        owner="owner"
        source={source}
      />,
    ),
  );
}
function click(label: string) {
  const button = [...host.querySelectorAll("button")].find((item) => item.textContent === label);
  if (!button) throw new Error("Missing recovery control");
  flushSync(() => button.click());
}

it("saves exact private payload before an explicit reload without submitting", async () => {
  const reload = vi.spyOn(window.location, "reload").mockImplementation(vi.fn<() => void>());
  const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
  renderPanel();
  expect(window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"))).toBeNull();
  click("Save recovery draft and reload");
  await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(
    JSON.parse(window.sessionStorage.getItem(markdownRecoveryStorageKey("owner")) ?? "null"),
  ).toMatchObject({ key: Operation.key, source: Source });
  expect(create).not.toHaveBeenCalled();
});

it("does not reload when storage cannot preserve the draft", async () => {
  const reload = vi.spyOn(window.location, "reload").mockImplementation(vi.fn<() => void>());
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
    throw new Error("quota");
  });
  renderPanel();
  click("Save recovery draft and reload");
  await vi.waitFor(() => expect(host).toHaveTextContent("Recovery could not complete"));
  expect(reload).not.toHaveBeenCalled();
});

it.each([false, true])("restores same-key recovery after reload, admitted=%s", async (admitted) => {
  const taskId = "66666666-6666-4666-8666-666666666666";
  const lookup = vi
    .spyOn(DOCUMENT_TRANSFORM_API, "admission")
    .mockResolvedValue(admitted ? { task_id: taskId, status: "pending" } : null);
  const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
  saveMarkdownRecovery(makeMarkdownRecovery("owner", Operation, Source));
  renderPanel(null);
  click("Restore saved recovery draft");
  await vi.waitFor(() =>
    expect(Restore).toHaveBeenCalledExactlyOnceWith(
      admitted ? { ...Operation, taskId } : Operation,
      admitted ? null : Source,
    ),
  );
  expect(lookup).toHaveBeenCalledExactlyOnceWith(Operation.key);
  expect(create).not.toHaveBeenCalled();
});

it("keeps the lock when lookup fails instead of replaying", async () => {
  vi.spyOn(DOCUMENT_TRANSFORM_API, "admission").mockRejectedValue(new Error("offline"));
  renderPanel();
  click("Check original admission");
  await vi.waitFor(() => expect(host).toHaveTextContent("Recovery could not complete"));
  expect(Restore).not.toHaveBeenCalled();
});

it("locks recovery synchronously across duplicate clicks", async () => {
  const pending =
    Promise.withResolvers<Awaited<ReturnType<typeof DOCUMENT_TRANSFORM_API.admission>>>();
  const lookup = vi.spyOn(DOCUMENT_TRANSFORM_API, "admission").mockReturnValue(pending.promise);
  renderPanel();
  const button = [...host.querySelectorAll("button")].find(
    (item) => item.textContent === "Check original admission",
  );
  if (!button) throw new Error("Missing admission control");
  flushSync(() => {
    button.click();
    button.click();
  });
  expect(lookup).toHaveBeenCalledExactlyOnceWith(Operation.key);
  pending.resolve(null);
  await vi.waitFor(() => expect(host).toHaveTextContent("No task was found yet"));
  expect(Restore).not.toHaveBeenCalled();
});

it("rescues an older tab using exact original values and the existing reservation", async () => {
  vi.spyOn(DOCUMENT_TRANSFORM_API, "admission").mockResolvedValue(null);
  const create = vi.spyOn(DOCUMENT_TRANSFORM_API, "create");
  renderPanel(null);
  for (const [label, value] of [
    ["Original source name", Source.name],
    ["Original destination folder ID", Source.destinationFolderId],
    ["Exact original Markdown", Source.content],
  ] as const) {
    const field = [...host.querySelectorAll("label")].find(
      (candidate) => candidate.textContent.trim() === label,
    )?.control;
    if (!field) throw new Error("Missing labeled original-value control");
    // eslint-disable-next-line @typescript-eslint/unbound-method -- The native setter is immediately rebound to this exact test control.
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(field), "value")?.set;
    if (!setter) throw new Error("Missing native setter");
    flushSync(() => {
      setter.call(field, value);
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  click("Restore original values with reserved key");
  await vi.waitFor(() => expect(Restore).toHaveBeenCalledExactlyOnceWith(Operation, Source));
  expect(create).not.toHaveBeenCalled();
  expect(window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"))).toBeNull();
});
