"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { useRecentPagesScope } from "@/components/recent-pages/recent-pages-session";
import { handlePrefetchError } from "@/lib/handle-prefetch-error";
import { readRecentDocs } from "@/lib/last-doc";
import { recentPagesQueryOptions } from "@/lib/recent-pages-query";
import { allowsReusableSpeculation, readNavigatorHints } from "@/lib/speculation-policy";

const PREFETCH_DELAY_MS = 150;

interface RecentIntentState {
  committed: boolean;
  focused: boolean;
  hovered: boolean;
  timer: null | number;
}

export function RecentPagesLink({
  children,
  className,
  onNavigate,
}: {
  children: React.ReactNode;
  className?: string;
  onNavigate?: React.ComponentProps<typeof Link>["onNavigate"];
}) {
  const scope = useRecentPagesScope();
  const router = useRouter();
  const state = useRef<RecentIntentState>({
    committed: false,
    focused: false,
    hovered: false,
    timer: null,
  });
  const clearTimer = () => clearRecentIntentTimer(state.current);
  const prefetch = () => {
    if (!scope?.isActive()) return;
    router.prefetch("/");
    const ids = readRecentDocs(scope.userId).map((visit) => visit.fileId);
    void scope.queryClient.query(recentPagesQueryOptions(scope, ids)).catch(handlePrefetchError);
  };
  const schedule = () => {
    if (state.current.committed || state.current.timer !== null || !allowsSpeculation()) return;
    state.current.timer = window.setTimeout(() => {
      state.current.timer = null;
      if ((state.current.focused || state.current.hovered) && allowsSpeculation()) prefetch();
    }, PREFETCH_DELAY_MS);
  };
  const endIfIdle = () => {
    if (state.current.focused || state.current.hovered) return;
    state.current.committed = false;
    clearTimer();
  };
  const commit = () => {
    state.current.committed = true;
    clearTimer();
    prefetch();
  };
  useEffect(() => {
    const current = state.current;
    current.committed = false;
    current.focused = false;
    current.hovered = false;
    clearRecentIntentTimer(current);
    return () => clearRecentIntentTimer(current);
  }, [scope]);
  return (
    <Link
      aria-label="Recent pages"
      className={className}
      href="/"
      onBlur={() => {
        state.current.focused = false;
        endIfIdle();
      }}
      onClick={(event) => {
        if (event.detail === 0) commit();
      }}
      onFocus={() => {
        state.current.focused = true;
        schedule();
      }}
      {...(onNavigate ? { onNavigate } : {})}
      onPointerDown={(event) => {
        if (
          event.button === 0 &&
          !event.altKey &&
          !event.ctrlKey &&
          !event.metaKey &&
          !event.shiftKey
        ) {
          commit();
        }
      }}
      onPointerEnter={(event) => {
        if (!isHoverPointer(event.pointerType)) return;
        state.current.hovered = true;
        schedule();
      }}
      onPointerLeave={(event) => {
        if (!isHoverPointer(event.pointerType)) return;
        state.current.hovered = false;
        endIfIdle();
      }}
      prefetch={false}
    >
      {children}
    </Link>
  );
}

function allowsSpeculation(): boolean {
  return (
    document.visibilityState === "visible" &&
    allowsReusableSpeculation(readNavigatorHints(navigator))
  );
}

function isHoverPointer(pointerType: string): boolean {
  return pointerType === "" || pointerType === "mouse" || pointerType === "pen";
}

function clearRecentIntentTimer(state: RecentIntentState): void {
  if (state.timer !== null) window.clearTimeout(state.timer);
  state.timer = null;
}
