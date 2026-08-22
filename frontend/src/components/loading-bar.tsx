"use client";

import { useIsFetching } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const MAX_VISIBLE_MS = 30_000;
const SHOW_DELAY_MS = 150;

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
  if (foregroundFetching === 0) return null;
  return <TimedLoadingBar key={pathname} />;
}

function TimedLoadingBar() {
  const [expired, setExpired] = useState(false);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const showTimer = window.setTimeout(() => {
      setVisible(true);
    }, SHOW_DELAY_MS);
    const timer = window.setTimeout(() => {
      setExpired(true);
    }, MAX_VISIBLE_MS);
    return () => {
      window.clearTimeout(showTimer);
      window.clearTimeout(timer);
    };
  }, []);
  if (expired || !visible) return null;
  return (
    <div
      aria-label="Loading document"
      className="fixed top-0 right-0 left-0 z-[9999] h-0.5"
      role="progressbar"
    >
      <div className="bg-primary animate-loading-bar h-full motion-reduce:w-full motion-reduce:animate-none" />
    </div>
  );
}
