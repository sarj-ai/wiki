import { toast } from "sonner";

export async function runWithErrorToast(
  fn: () => Promise<void>,
  fallback: ((err: unknown) => string) | string,
): Promise<void> {
  try {
    await fn();
  } catch (err) {
    if (isAbortError(err)) return;
    toast.error(typeof fallback === "function" ? fallback(err) : toErrorMessage(err, fallback));
  }
}

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export function toErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}
