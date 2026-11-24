import type { DocumentTransformTask } from "@/lib/document-transform-types";

export function transformProgressMessage(
  task: DocumentTransformTask,
): string | null {
  if (task.status === "pending") return "Queued for a worker…";
  if (task.status !== "running") return null;
  const attempt = task.attemptCount;
  switch (task.executionState) {
    case "retrying":
      return `Waiting to retry after attempt ${attempt}…`;
    case "awaiting_model":
      return `Polishing in progress: waiting for the current Gemini round (attempt ${attempt})…`;
    case "running":
      return `Running and checking the document (attempt ${attempt})…`;
  }
}
