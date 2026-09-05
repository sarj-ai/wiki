import { clearUserBrowserStorage } from "@/lib/browser-storage";
import { installLocalStorage } from "@/test-stubs/local-storage";
import {
  clearMarkdownRecovery,
  makeMarkdownRecovery,
  saveMarkdownRecovery,
  validateMarkdownRecovery,
  markdownRecoveryStorageKey,
  REPLAY_MAX_AGE_MS,
} from "@/lib/markdown-transform-recovery";
import type { TransformRecovery } from "@/lib/document-transform-recovery";

const operation: TransformRecovery = {
  createdAt: Date.now(),
  key: "55555555-5555-4555-8555-555555555555",
  mode: "unslop",
  outputStrategy: "copy",
  outputName: "Exact output",
  source: null,
  taskId: null,
};
const source = {
  kind: "markdown",
  content: "# Private\n\n  Exact 😀 text\r\n",
  name: "Exact source",
  destinationFolderId: "folder",
};

beforeEach(() => {
  installLocalStorage();
});

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

it("persists exact payload only on explicit save and clears it on signout", () => {
  const localWrite = vi.spyOn(window.localStorage, "setItem");
  const bundle = makeMarkdownRecovery("owner", operation, source);
  expect(window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"))).toBeNull();
  saveMarkdownRecovery(bundle);
  const raw = window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"));
  expect(raw).not.toBeNull();
  expect(validateMarkdownRecovery(raw!, "owner", operation).source).toEqual(source);
  expect(localWrite).not.toHaveBeenCalled();
  clearUserBrowserStorage("owner");
  expect(window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"))).toBeNull();
});

it.each([
  { owner: "other" },
  { key: "66666666-6666-4666-8666-666666666666" },
  { mode: "format" },
  { outputName: "Changed" },
  { createdAt: operation.createdAt - REPLAY_MAX_AGE_MS },
])("rejects mismatched recovery metadata %j", (change) => {
  const raw = JSON.stringify({ ...makeMarkdownRecovery("owner", operation, source), ...change });
  expect(() => validateMarkdownRecovery(raw, "owner", operation)).toThrow("does not match");
});

it("rejects expired recovery even with matching operation metadata", () => {
  const expired = { ...operation, createdAt: Date.now() - REPLAY_MAX_AGE_MS };
  expect(() => makeMarkdownRecovery("owner", expired, source)).toThrow("expired");
});

it("fails closed on quota failure", () => {
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
    throw new DOMException("quota", "QuotaExceededError");
  });
  expect(() => saveMarkdownRecovery(makeMarkdownRecovery("owner", operation, source))).toThrow(
    "quota",
  );
});

it.each(["bad json", "{}", '{"secret":"never echo"}'])(
  "rejects malformed bundles without echoing payload",
  (raw) => {
    expect(() => validateMarkdownRecovery(raw, "owner", operation)).toThrow();
  },
);

it("does not block admitted-task recovery when cleanup storage is unavailable", () => {
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
    throw new Error("blocked storage");
  });
  expect(() => clearMarkdownRecovery("owner")).not.toThrow();
});
