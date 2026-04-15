"use client";

import { useEffect } from "react";

const STORAGE_KEY = "navigator-sidebar-width";
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

export function SidebarResizeHandle() {
  useRestoreSidebarWidth();

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    const sidebar = document.querySelector<HTMLElement>('[data-slot="sidebar-inner"]');
    if (!wrapper || !sidebar) return;

    // Measure the current rendered width. Using getBoundingClientRect
    // rather than the CSS var so we start from whatever the user sees,
    // not whatever rem-value was cached.
    const startX = event.clientX;
    const startWidth = sidebar.getBoundingClientRect().width;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    const onMove = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX;
      const next = Math.max(MIN_WIDTH_PX, Math.min(MAX_WIDTH_PX, startWidth + delta));
      wrapper.style.setProperty("--sidebar-width", `${next}px`);
    };

    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      // Persist the final width as a pixel value — keeps intent stable
      // across sessions even if the user changes their root font-size.
      const final = wrapper.style.getPropertyValue("--sidebar-width");
      if (final) window.localStorage.setItem(STORAGE_KEY, final);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div
      onPointerDown={handlePointerDown}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      className="hover:bg-sidebar-border/80 absolute top-0 right-0 z-20 h-full w-1 cursor-col-resize transition-colors"
    />
  );
}
