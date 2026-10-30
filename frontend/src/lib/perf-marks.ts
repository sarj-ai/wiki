import { sendPerformanceEvent } from "@/lib/clients/send-performance-event";

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

const PERF_ENABLED = readPerfFlag();

type ReportablePhase = "commit" | "iframeLoad" | "iframeMount" | "toolbarPaint";

interface NavigationSample {
  navigationId: string;
  reportedPhases: Set<ReportablePhase>;
  startedAt: number;
  timeout: number;
}

const NAVIGATION_SAMPLES = new Map<string, NavigationSample>();
const RUM_SAMPLE_THRESHOLD = 26;
const SAMPLE_MAX_AGE_MS = 120_000;

export function markNav(id: string, phase: string, extra: null | PerfExtra = null): void {
  if (typeof window === "undefined") return;
  const sample = NAVIGATION_SAMPLES.get(id);
  if (!PERF_ENABLED && phase !== "click" && sample === undefined) return;
  const now = performance.now();
  if (PERF_ENABLED) {
    // eslint-disable-next-line unicorn/no-global-object-property-assignment -- This opt-in probe intentionally records navigation timings on window.
    (window.__sarjPerf ??= []).push({ extra, id, phase, t: now });
  }
  if (phase === "click") {
    startSample(id, now);
    return;
  }
  if (!sample || !["commit", "toolbarPaint", "iframeMount", "iframeLoad"].includes(phase)) return;
  if (phase === "iframeLoad") {
    NAVIGATION_SAMPLES.delete(id);
    window.clearTimeout(sample.timeout);
  }
  reportPhase(sample, phase, now - sample.startedAt);
}

export function markPaint(id: string, phase: string): void {
  if (typeof window === "undefined") return;
  const sample = NAVIGATION_SAMPLES.get(id);
  if (!PERF_ENABLED && sample === undefined) return;
  requestAnimationFrame(() => {
    if (NAVIGATION_SAMPLES.get(id) !== sample) return;
    requestAnimationFrame(() => {
      if (NAVIGATION_SAMPLES.get(id) !== sample) return;
      markNav(id, phase);
    });
  });
}

function startSample(id: string, startedAt: number): void {
  const previous = NAVIGATION_SAMPLES.get(id);
  if (previous) window.clearTimeout(previous.timeout);
  NAVIGATION_SAMPLES.delete(id);
  const navigationId = crypto.randomUUID();
  if (Number.parseInt(navigationId.slice(0, 2), 16) >= RUM_SAMPLE_THRESHOLD) return;
  const timeout = window.setTimeout(() => {
    NAVIGATION_SAMPLES.delete(id);
  }, SAMPLE_MAX_AGE_MS);
  const sample: NavigationSample = {
    navigationId,
    reportedPhases: new Set(),
    startedAt,
    timeout,
  };
  NAVIGATION_SAMPLES.set(id, sample);
}

function reportPhase(sample: NavigationSample, phase: string, durationMs: number): void {
  if (!isReportablePhase(phase) || sample.reportedPhases.has(phase)) return;
  sample.reportedPhases.add(phase);
  sendPerformanceEvent({
    durationMs,
    navigationId: sample.navigationId,
    phase,
    saveData: readSaveData(),
    visibility: document.visibilityState === "hidden" ? "hidden" : "visible",
  });
}

function isReportablePhase(phase: string): phase is ReportablePhase {
  return ["commit", "iframeLoad", "iframeMount", "toolbarPaint"].includes(phase);
}

function readSaveData(): boolean | undefined {
  const connection: unknown = Reflect.get(navigator, "connection");
  const saveData: unknown =
    typeof connection === "object" && connection !== null
      ? Reflect.get(connection, "saveData")
      : null;
  let result: boolean | undefined;
  if (typeof saveData === "boolean") result = saveData;
  return result;
}
