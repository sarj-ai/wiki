import { readTransformRecovery, writeTransformRecovery } from "./document-transform-recovery";

describe("document transform recovery", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("isolates task handles by authenticated owner and document", () => {
    const operation = {
      createdAt: Date.now(),
      key: "66666666-6666-4666-8666-666666666666",
      mode: "format",
      outputStrategy: "copy",
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
