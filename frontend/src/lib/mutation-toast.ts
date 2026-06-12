import { toast } from "sonner";

/**
 * Run a mutation flow, toasting on failure instead of rethrowing.
 * Success-side effects belong inside ``fn``. A function ``fallback``
 * produces the complete message (takes precedence over err.message).
 */
export async function runWithErrorToast(
  fn: () => Promise<void>,
  fallback: string | ((err: unknown) => string),
): Promise<void> {
  try {
    await fn();
  } catch (err) {
    toast.error(typeof fallback === "function" ? fallback(err) : toErrorMessage(err, fallback));
  }
}

export function toErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}
