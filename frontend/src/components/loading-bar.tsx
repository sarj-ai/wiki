"use client";

import { useIsFetching } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { useLayoutEffect, useRef, useState } from "react";

import { useDocViewerStore, viewerIdentity } from "@/components/wiki-document/doc-viewer-store";

const MAX_VISIBLE_MS = 30_000;
const SHOW_DELAY_MS = 150;
const MIN_VISIBLE_MS = 300;

type LoadingPhase = "hidden" | "suppressed" | "visible";

interface LoadingQuery {
  getObserversCount: () => number;
  meta: Record<string, unknown> | undefined;
  state: { data: unknown };
}

export function isForegroundPrimaryLoad(query: LoadingQuery): boolean {
  return (
    query.meta?.["foregroundPrimaryLoad"] === true &&
    query.state.data === undefined &&
    query.getObserversCount() > 0
  );
}

export function GlobalLoadingBar() {
  const pathname = usePathname();
  const foregroundFetching = useIsFetching({
    predicate: isForegroundPrimaryLoad,
  });
  const activeViewerLoading = useDocViewerStore((state) => {
    if (state.active === null) return false;
    return state.viewerLoads[viewerIdentity(state.active)]?.status === "loading";
  });
  return (
    <TimedLoadingBar active={foregroundFetching > 0 || activeViewerLoading} identity={pathname} />
  );
}

function TimedLoadingBar({ active, identity }: { active: boolean; identity: string }) {
  const [phase, setPhaseState] = useState<LoadingPhase>("hidden");
  const phaseRef = useRef<LoadingPhase>("hidden");
  const cycleStartedAt = useRef(0);
  const generationRef = useRef(0);
  const previousIdentity = useRef(identity);
  const visibleSince = useRef(0);

  useLayoutEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    const timers: number[] = [];
    const identityChanged = previousIdentity.current !== identity;
    previousIdentity.current = identity;

    const setPhase = (next: LoadingPhase) => {
      phaseRef.current = next;
      setPhaseState(next);
    };
    const isCurrent = () => generationRef.current === generation;

    if (!active) {
      if (phaseRef.current === "visible") {
        const remaining = Math.max(0, visibleSince.current + MIN_VISIBLE_MS - Date.now());
        if (remaining === 0) setPhase("hidden");
        else {
          timers.push(
            window.setTimeout(() => {
              if (isCurrent()) setPhase("hidden");
            }, remaining),
          );
        }
      } else if (phaseRef.current === "suppressed") {
        setPhase("hidden");
      }
      return () => {
        for (const timer of timers) window.clearTimeout(timer);
      };
    }

    if (identityChanged) {
      cycleStartedAt.current = Date.now();
      if (phaseRef.current === "suppressed") setPhase("hidden");
    }
    if (phaseRef.current === "suppressed" && !identityChanged) return;

    if (phaseRef.current === "hidden") {
      cycleStartedAt.current = Date.now();
      timers.push(
        window.setTimeout(() => {
          if (!isCurrent()) return;
          if (Date.now() >= cycleStartedAt.current + MAX_VISIBLE_MS) {
            setPhase("suppressed");
            return;
          }
          visibleSince.current = Date.now();
          setPhase("visible");
        }, SHOW_DELAY_MS),
      );
    }

    const watchdogRemaining = Math.max(0, cycleStartedAt.current + MAX_VISIBLE_MS - Date.now());
    timers.push(
      window.setTimeout(() => {
        if (isCurrent()) setPhase("suppressed");
      }, watchdogRemaining),
    );

    return () => {
      if (generationRef.current === generation) generationRef.current += 1;
      for (const timer of timers) window.clearTimeout(timer);
    };
  }, [active, identity]);

  return (
    <>
      <span aria-atomic="true" aria-live="polite" className="sr-only" role="status">
        {phase === "visible"
          ? "Loading document"
          : phase === "suppressed"
            ? "Still loading document"
            : ""}
      </span>
      {phase === "visible" ? (
        <div
          aria-hidden="true"
          className="bg-primary/15 pointer-events-none fixed top-0 right-0 left-0 z-[9999] h-0.5 overflow-hidden"
          data-loading-indicator="true"
        >
          <div
            className="bg-primary animate-loading-bar absolute inset-y-0 left-0 w-1/3 will-change-transform"
            key={identity}
          />
        </div>
      ) : null}
    </>
  );
}
