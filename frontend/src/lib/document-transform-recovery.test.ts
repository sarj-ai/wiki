import { installLocalStorage } from "@/test-stubs/install-local-storage";

import {
  claimTransformRecovery,
  completeTransformRecovery,
  readTransformRecovery,
  recordTransformTask,
  type TransformRecovery,
  writeTransformRecovery,
} from "./document-transform-recovery";

describe("document transform recovery", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    installLocalStorage();
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async (_name: string, callback: () => unknown) => {
          await new Promise<void>((resolve) => {
            queueMicrotask(resolve);
          });
          return callback();
        },
      },
    });
  });

  const firstKey = "66666666-6666-4666-8666-666666666666";
  const secondKey = "77777777-7777-4777-8777-777777777777";

  it("two clients converge on the same durable request after a lost response or reload", async () => {
    const [first, second] = await Promise.all([
      claimTransformRecovery("owner", "drive:doc", candidate(firstKey)),
      claimTransformRecovery("owner", "drive:doc", candidate(secondKey)),
    ]);
    expect(second).toEqual(first);
    window.sessionStorage.clear();
    expect(readTransformRecovery("owner", "drive:doc")).toEqual(first);
    expect(readTransformRecovery("other", "drive:doc")).toBeNull();
    await expect(
      claimTransformRecovery("owner", "drive:doc", candidate(secondKey, Date.now())),
    ).resolves.toEqual(first);
  });

  it("releases only the exact terminal operation, permitting a deliberate later run", async () => {
    await claimTransformRecovery("owner", "drive:doc", candidate(firstKey));
    await completeTransformRecovery("owner", "drive:doc", secondKey);
    expect(readTransformRecovery("owner", "drive:doc")?.key).toBe(firstKey);
    await completeTransformRecovery("owner", "drive:doc", firstKey);
    expect(readTransformRecovery("owner", "drive:doc")).toBeNull();
    const delayed = await claimTransformRecovery("owner", "drive:doc", candidate(secondKey));
    expect(delayed.key).toBe(firstKey);
    const deliberate = await claimTransformRecovery(
      "owner",
      "drive:doc",
      candidate(secondKey, Date.now() + 1),
    );
    expect(deliberate.key).toBe(secondKey);
    await completeTransformRecovery("owner", "drive:doc", firstKey);
    expect(readTransformRecovery("owner", "drive:doc")?.key).toBe(secondKey);
  });

  it("shares the admitted task handle without overwriting another operation or terminal state", async () => {
    const operation = await claimTransformRecovery("owner", "drive:doc", candidate(firstKey));
    const taskId = "55555555-5555-4555-8555-555555555555";
    await recordTransformTask("owner", "drive:doc", { ...operation, key: secondKey, taskId });
    expect(readTransformRecovery("owner", "drive:doc")?.taskId).toBeNull();
    await recordTransformTask("owner", "drive:doc", { ...operation, taskId });
    window.sessionStorage.clear();
    expect(readTransformRecovery("owner", "drive:doc")?.taskId).toBe(taskId);
    await expect(
      recordTransformTask("owner", "drive:doc", { ...operation, taskId: secondKey }),
    ).rejects.toThrow("does not match");
    await completeTransformRecovery("owner", "drive:doc", firstKey);
    await recordTransformTask("owner", "drive:doc", { ...operation, taskId });
    expect(readTransformRecovery("owner", "drive:doc")).toBeNull();
  });

  it("fails closed without atomic coordination but permits recovery", async () => {
    const existing = await claimTransformRecovery("owner", "drive:existing", candidate(firstKey));
    Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    await expect(
      claimTransformRecovery("owner", "drive:existing", candidate(secondKey)),
    ).resolves.toEqual(existing);
    await expect(claimTransformRecovery("owner", "drive:doc", candidate(firstKey))).rejects.toThrow(
      "Web Locks",
    );
    await expect(claimTransformRecovery(null, "drive:doc", candidate(firstKey))).rejects.toThrow(
      "Sign in",
    );
  });

  it("does not overwrite corrupt shared state or bypass unavailable storage", async () => {
    window.localStorage.setItem("sarj-wiki:transform:owner:drive%3Adoc:shared", "{invalid");
    await expect(
      claimTransformRecovery("owner", "drive:doc", candidate(firstKey)),
    ).rejects.toThrow();
    expect(() => readTransformRecovery("owner", "drive:doc")).toThrow();
    vi.spyOn(window.localStorage, "setItem").mockImplementationOnce(() => {
      throw new Error("Storage unavailable");
    });
    await expect(
      claimTransformRecovery("owner", "drive:other", candidate(firstKey)),
    ).rejects.toThrow("Storage unavailable");
  });

  it("isolates task handles by authenticated owner and document", () => {
    const operation = {
      createdAt: Date.now(),
      key: "66666666-6666-4666-8666-666666666666",
      mode: "format",
      outputStrategy: "copy",
      outputName: "Reviewed architecture",
      source: null,
      taskId: "55555555-5555-4555-8555-555555555555",
    } as const;
    writeTransformRecovery("first", "markdown", operation);
    expect(readTransformRecovery("first", "markdown")).toEqual(operation);
    expect(readTransformRecovery("second", "markdown")).toBeNull();
    expect(readTransformRecovery("first", "drive:other")).toBeNull();
    expect(readTransformRecovery(null, "markdown")).toBeNull();
    writeTransformRecovery("first", "markdown", null);
    expect(readTransformRecovery("first", "markdown")).toBeNull();
  });

  it("fails closed when stored state is corrupted", () => {
    window.sessionStorage.setItem("sarj-wiki:transform:first:markdown", "{invalid");
    expect(() => readTransformRecovery("first", "markdown")).toThrow();
  });
});

function candidate(key: string, createdAt = 1): TransformRecovery {
  return {
    key,
    createdAt,
    mode: "format",
    outputStrategy: "copy",
    source: {
      kind: "drive_file",
      fileId: "doc",
      name: "Document",
      expectedModifiedTime: "revision-1",
    },
    taskId: null,
  };
}
