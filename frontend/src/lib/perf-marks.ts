type PerfExtra = Record<string, string | number | boolean>;

interface PerfRecord {
  id: string;
  phase: string;
  t: number;
  extra: PerfExtra | null;
}

declare global {
  interface Window {
    __sarjPerf?: PerfRecord[];
  }
}

const PERF_FLAG_KEY = "sarj-perf";

function readPerfFlag(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (new URLSearchParams(window.location.search).get("perf") === "1") return true;
    return window.localStorage.getItem(PERF_FLAG_KEY) === "1";
  } catch {
    return false;
  }
}

const enabled = readPerfFlag();

export function markNav(id: string, phase: string, extra: PerfExtra | null = null): void {
  if (!enabled || typeof window === "undefined") return;
  (window.__sarjPerf ??= []).push({ id, phase, t: performance.now(), extra });
}

export function markPaint(id: string, phase: string): void {
  if (!enabled || typeof window === "undefined") return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      markNav(id, phase);
    });
  });
}
