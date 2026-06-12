"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "wiki-sidebar-width";
const MIN_WIDTH_PX = 224; // 14rem — below this the labels clip too hard
const MAX_WIDTH_PX = 576; // 36rem — beyond this the viewer gets cramped

// Reads the persisted width on mount and applies it to the shadcn
// SidebarProvider wrapper (which sets --sidebar-width via inline style).
// Runs once per session — subsequent drags update the CSS var directly.
function useRestoreSidebarWidth() {
  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (!saved) return;
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    wrapper?.style.setProperty("--sidebar-width", saved);
  }, []);
}

// Drag-to-resize with live width updates. A fullscreen invisible
// overlay is rendered while the drag is active so iframes inside the
// main content area can't steal pointermove events — without it the
// drag stalls the moment the cursor crosses the Google Docs iframe.
// Width changes are rAF-batched so the sidebar only re-lays out once
// per frame regardless of how many pointer events arrive.
export function SidebarResizeHandle() {
  useRestoreSidebarWidth();
  const [dragging, setDragging] = useState(false);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    const sidebar = document.querySelector<HTMLElement>('[data-slot="sidebar-inner"]');
    if (!wrapper || !sidebar) return;

    const startX = event.clientX;
    const startWidth = sidebar.getBoundingClientRect().width;
    const clamp = (px: number) => Math.max(MIN_WIDTH_PX, Math.min(MAX_WIDTH_PX, px));

    // Pin the shadcn width-transition to none while we drag so every
    // mousemove lands as a direct paint rather than queuing a 200ms
    // tween behind the live cursor.
    const widthElements = document.querySelectorAll<HTMLElement>(
      '[data-slot="sidebar-gap"], [data-slot="sidebar-container"]',
    );
    widthElements.forEach((el) => {
      el.style.transition = "none";
    });
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    setDragging(true);

    let pendingX: number | null = null;
    let rafId: number | null = null;
    const apply = () => {
      rafId = null;
      if (pendingX === null) return;
      const next = clamp(startWidth + (pendingX - startX));
      wrapper.style.setProperty("--sidebar-width", `${next}px`);
      pendingX = null;
    };

    const onMove = (moveEvent: PointerEvent) => {
      pendingX = moveEvent.clientX;
      rafId ??= requestAnimationFrame(apply);
    };

    const onUp = (upEvent: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (rafId !== null) cancelAnimationFrame(rafId);
      pendingX = upEvent.clientX;
      apply();
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      widthElements.forEach((el) => {
        el.style.transition = "";
      });
      const final = wrapper.style.getPropertyValue("--sidebar-width");
      if (final) window.localStorage.setItem(STORAGE_KEY, final);
      setDragging(false);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <>
      <div
        onPointerDown={handlePointerDown}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        className="hover:bg-sidebar-border/80 absolute top-0 right-0 z-20 h-full w-1 cursor-col-resize transition-colors"
      />
      {/*
        Fullscreen invisible overlay captured while dragging. Without
        this, moving the pointer over the Google Docs iframe transfers
        pointer ownership to the iframe's document and our pointermove
        handler stops firing — the drag visibly stalls the moment the
        cursor crosses into the viewer area. The overlay sits at a
        z-index above the iframe and passes through to the real resize
        handler via the same pointer capture path.
      */}
      {dragging && (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-50 cursor-col-resize"
          // Background must be non-transparent (even if 0-opacity) for
          // some browsers to treat it as a pointer-event target.
          style={{ backgroundColor: "rgba(0,0,0,0.001)" }}
        />
      )}
    </>
  );
}
