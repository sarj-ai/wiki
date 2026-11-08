import { toast } from "sonner";
import { z } from "zod";

const ErrorFallbackSchema = z.function();

export async function runWithErrorToast(
  fn: () => Promise<void>,
  fallback: ((err: unknown) => string) | string,
): Promise<void> {
  try {
    await fn();
  } catch (err) {
    if (isAbortError(err)) return;
    toast.error(
      isErrorFallback(fallback) ? fallback(err) : toErrorMessage(err, fallback),
    );
  }
}

function isErrorFallback(
  value: ((err: unknown) => string) | string,
): value is (err: unknown) => string {
  return ErrorFallbackSchema.safeParse(value).success;
}

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export function toErrorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}
