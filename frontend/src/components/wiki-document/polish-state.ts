import type { DocumentTransformResult } from "@/lib/document-transform-types";

type OutputReference =
  | { readonly kind: "unknown" }
  | { readonly kind: "original" | "copy"; readonly fileId: string };

export type PolishState =
  | { readonly kind: "idle" }
  | { readonly kind: "starting" }
  | { readonly kind: "transforming"; readonly taskId: string }
  | { readonly kind: "retryable"; readonly message: string }
  | { readonly kind: "completed"; readonly result: DocumentTransformResult }
  | {
      readonly kind: "review";
      readonly message: string;
      readonly taskId: string | null;
      readonly result: DocumentTransformResult;
    }
  | {
      readonly kind: "review-without-output";
      readonly message: string;
      readonly taskId: string | null;
    }
  | {
      readonly kind: "failed";
      readonly message: string;
      readonly taskId: string | null;
      readonly output: OutputReference;
    };

export type PolishEvent =
  | { readonly type: "start" | "finish" | "reset" }
  | { readonly type: "admitted"; readonly taskId: string }
  | {
      readonly type: "completed" | "short-link-ready";
      readonly result: DocumentTransformResult;
    }
  | { readonly type: "retryable"; readonly message: string }
  | {
      readonly type: "review";
      readonly message: string;
      readonly taskId: string | null;
      readonly result: DocumentTransformResult | null;
    }
  | {
      readonly type: "failed";
      readonly message: string;
      readonly taskId: string | null;
      readonly output: OutputReference;
    };

function unreachable(value: never): never {
  throw new Error(`Unsupported polish event: ${String(value)}`);
}

export function isPolishRunning(
  state: PolishState,
): state is Extract<PolishState, { kind: "starting" | "transforming" }> {
  return state.kind === "starting" || state.kind === "transforming";
}

/** Late completion/finish events are ignored after close, reset, or a terminal outcome. */
export function transitionPolish(
  state: PolishState,
  event: PolishEvent,
): PolishState {
  switch (event.type) {
    case "reset":
      return { kind: "idle" };
    case "start":
      return state.kind === "idle" || state.kind === "retryable"
        ? { kind: "starting" }
        : state;
    case "finish":
      return isPolishRunning(state) ? { kind: "idle" } : state;
    case "admitted":
      return state.kind === "starting"
        ? { kind: "transforming", taskId: event.taskId }
        : state;
    case "completed":
      return state.kind === "transforming"
        ? { kind: "completed", result: event.result }
        : state;
    case "retryable":
      return isPolishRunning(state) ||
        state.kind === "idle" ||
        state.kind === "retryable"
        ? { kind: "retryable", message: event.message }
        : state;
    case "review":
      if (!isPolishRunning(state)) return state;
      return event.result === null
        ? {
            kind: "review-without-output",
            message: event.message,
            taskId: event.taskId,
          }
        : {
            kind: "review",
            message: event.message,
            taskId: event.taskId,
            result: event.result,
          };
    case "failed":
      return isPolishRunning(state)
        ? {
            kind: "failed",
            message: event.message,
            taskId: event.taskId,
            output: event.output,
          }
        : state;
    case "short-link-ready":
      return (state.kind === "completed" || state.kind === "review") &&
        state.result.operationId === event.result.operationId
        ? { ...state, result: event.result }
        : state;
    default:
      return unreachable(event);
  }
}
