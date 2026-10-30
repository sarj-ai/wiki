import { discardStream } from "@/lib/discard-stream";

interface PerformanceEvent {
  durationMs: number;
  navigationId: string;
  phase: string;
  saveData: boolean | undefined;
  visibility: "hidden" | "visible";
}

export function sendPerformanceEvent(event: PerformanceEvent): void {
  const payload = JSON.stringify(event);
  if (navigator.sendBeacon("/api/perf", new Blob([payload], { type: "application/json" }))) {
    return;
  }
  void (async () => {
    const response = await fetch("/api/perf", {
      body: payload,
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      method: "POST",
      signal: AbortSignal.timeout(5000),
    });
    discardStream(response.body);
  })().catch((error: unknown) => {
    console.warn("Performance event delivery failed", error);
  });
}
