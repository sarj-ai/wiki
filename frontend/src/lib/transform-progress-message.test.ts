import { describe, expect, it } from "vitest";

import { transformProgressMessage } from "@/lib/transform-progress-message";

describe("transform task progress", () => {
  it("46 distinguishes queue wait, active work, and retry wait", () => {
    expect(
      transformProgressMessage({ id: "task", status: "pending" }),
    ).toContain("Queued");
    expect(
      transformProgressMessage({
        id: "task",
        status: "running",
        attemptCount: 2,
        executionState: "running",
      }),
    ).toContain("attempt 2");
    expect(
      transformProgressMessage({
        id: "task",
        status: "running",
        attemptCount: 1,
        executionState: "retrying",
      }),
    ).toContain("Waiting to retry after attempt 1");
  });

  it("49 does not describe a terminal task as ongoing work", () => {
    expect(
      transformProgressMessage({
        id: "task",
        status: "failed",
        errorCode: "model_output_invalid",
      }),
    ).toBeNull();
    expect(
      transformProgressMessage({ id: "task", status: "cancelled" }),
    ).toBeNull();
  });
});
