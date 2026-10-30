// Cleanup must not delay authentication recovery or replace an HTTP error.
export function discardStream(body: null | ReadableStream<Uint8Array>): void {
  if (!body) return;
  void body.cancel().catch(() => null);
}
