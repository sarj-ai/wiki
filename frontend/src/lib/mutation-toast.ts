import { toast } from "sonner";

export async function runWithErrorToast(
  fn: () => Promise<void>,
  fallback: ((err: unknown) => string) | string,
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
