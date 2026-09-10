import { clearUserBrowserStorage } from "@/lib/browser-storage";
import type { TransformRecovery } from "@/lib/document-transform-recovery";
import {
  clearMarkdownRecovery,
  makeMarkdownRecovery,
  markdownRecoveryStorageKey,
  REPLAY_MAX_AGE_MS,
  saveMarkdownRecovery,
  validateMarkdownRecovery,
} from "@/lib/markdown-transform-recovery";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

const OPERATION: TransformRecovery = {
  createdAt: Date.now(),
  key: "55555555-5555-4555-8555-555555555555",
  mode: "unslop",
  outputStrategy: "copy",
  outputName: "Exact output",
  source: null,
  taskId: null,
};
const SOURCE = {
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
  const bundle = makeMarkdownRecovery("owner", OPERATION, SOURCE);
  expect(window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"))).toBeNull();
  saveMarkdownRecovery(bundle);
  const raw = window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"));
  expect(validateMarkdownRecovery(raw ?? "", "owner", OPERATION).source).toEqual(SOURCE);
  expect(localWrite).not.toHaveBeenCalled();
  clearUserBrowserStorage("owner");
  expect(window.sessionStorage.getItem(markdownRecoveryStorageKey("owner"))).toBeNull();
});

it.each([
  { owner: "other" },
  { key: "66666666-6666-4666-8666-666666666666" },
  { mode: "format" },
  { outputName: "Changed" },
  { createdAt: OPERATION.createdAt - REPLAY_MAX_AGE_MS },
])("rejects mismatched recovery metadata %j", (change) => {
  const raw = JSON.stringify({ ...makeMarkdownRecovery("owner", OPERATION, SOURCE), ...change });
  expect(() => validateMarkdownRecovery(raw, "owner", OPERATION)).toThrow("does not match");
});

it("rejects expired recovery even with matching operation metadata", () => {
  const expired = { ...OPERATION, createdAt: Date.now() - REPLAY_MAX_AGE_MS };
  expect(() => makeMarkdownRecovery("owner", expired, SOURCE)).toThrow("expired");
});

it("fails closed on quota failure", () => {
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
    throw new DOMException("quota", "QuotaExceededError");
  });
  expect(() => saveMarkdownRecovery(makeMarkdownRecovery("owner", OPERATION, SOURCE))).toThrow(
    "quota",
  );
});

it.each(["bad json", "{}", '{"secret":"never echo"}'])(
  "rejects malformed bundles without echoing payload",
  (raw) => {
    expect(() => validateMarkdownRecovery(raw, "owner", OPERATION)).toThrow();
  },
);

it("does not block admitted-task recovery when cleanup storage is unavailable", () => {
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
    throw new Error("blocked storage");
  });
  expect(() => clearMarkdownRecovery("owner")).not.toThrow();
});
