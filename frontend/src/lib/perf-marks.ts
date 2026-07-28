type PerfExtra = Record<string, boolean | number | string>;

interface PerfRecord {
  extra: null | PerfExtra;
  id: string;
  phase: string;
  t: number;
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

export function markNav(id: string, phase: string, extra: null | PerfExtra = null): void {
  if (!enabled || typeof window === "undefined") return;
  (window.__sarjPerf ??= []).push({ extra, id, phase, t: performance.now() });
}

export function markPaint(id: string, phase: string): void {
  if (!enabled || typeof window === "undefined") return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      markNav(id, phase);
    });
  });
}
