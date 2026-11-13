import type { DocumentTransformTask } from "@/lib/document-transform-types";

export function transformProgressMessage(
  task: DocumentTransformTask,
): string | null {
  if (task.status === "pending") return "Queued for a worker…";
  if (task.status !== "running") return null;
  const attempt = task.attemptCount;
  if (task.executionState === "retrying") {
    return `Waiting to retry after attempt ${attempt}…`;
  }
  return `Running and checking the document (attempt ${attempt})…`;
}
