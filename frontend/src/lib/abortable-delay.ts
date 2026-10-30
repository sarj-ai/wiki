// Polling waits remove their cancellation listener when the timer settles.
export async function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted();
  await new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeout);
      reject(
        signal?.reason instanceof Error
          ? signal.reason
          : new DOMException("The operation was aborted", "AbortError"),
      );
    };
    const timeout = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
