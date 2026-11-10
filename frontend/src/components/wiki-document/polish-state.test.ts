import { describe, expect, it } from "vitest";

import { type PolishState, transitionPolish } from "./polish-state";

describe("polish transitions", () => {
  it("requires admission before polling and ignores a late admission after close", () => {
    const idle: PolishState = { kind: "idle" };
    expect(transitionPolish(idle, { type: "admitted", taskId: "task" })).toBe(
      idle,
    );
    const starting = transitionPolish(idle, { type: "start" });
    const polling = transitionPolish(starting, {
      type: "admitted",
      taskId: "task",
    });
    expect(polling).toEqual({ kind: "transforming", taskId: "task" });
    const closed = transitionPolish(polling, { type: "reset" });
    expect(transitionPolish(closed, { type: "admitted", taskId: "task" })).toBe(
      closed,
    );
  });

  it("keeps a terminal failure across finally and competing outcomes", () => {
    const failed = transitionPolish(
      { kind: "starting" },
      {
        type: "failed",
        message: "Outcome unknown",
        taskId: null,
        output: { kind: "unknown" },
      },
    );
    expect(transitionPolish(failed, { type: "finish" })).toBe(failed);
    expect(
      transitionPolish(failed, { type: "retryable", message: "Retry" }),
    ).toBe(failed);
    expect(
      transitionPolish(failed, {
        type: "review",
        message: "Review",
        taskId: null,
        result: null,
      }),
    ).toBe(failed);
    expect(transitionPolish(failed, { type: "start" })).toBe(failed);
  });

  it("preserves actionable validation errors after clearing an inadmissible recovery", () => {
    const reset = transitionPolish({ kind: "starting" }, { type: "reset" });
    const retry = transitionPolish(reset, {
      type: "retryable",
      message: "Reload the document",
    });
    expect(transitionPolish(retry, { type: "finish" })).toBe(retry);
    expect(transitionPolish(retry, { type: "start" })).toEqual({
      kind: "starting",
    });
  });

  it("distinguishes a review without known output from a failure", () => {
    const review = transitionPolish(
      { kind: "transforming", taskId: "task" },
      {
        type: "review",
        message: "Check the preserved document",
        taskId: "task",
        result: null,
      },
    );
    expect(review).toEqual({
      kind: "review-without-output",
      message: "Check the preserved document",
      taskId: "task",
    });
    expect(transitionPolish(review, { type: "finish" })).toBe(review);
  });
});
