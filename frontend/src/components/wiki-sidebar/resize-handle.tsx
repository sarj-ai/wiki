"use client";

import { useEffect, useState } from "react";

import { ZSidebarWidthCss } from "@/lib/storage-schemas";

const STORAGE_KEY = "wiki-sidebar-width";
const MIN_WIDTH_PX = 224;
const MAX_WIDTH_PX = 576;

function useRestoreSidebarWidth() {
  useEffect(() => {
    const saved = ZSidebarWidthCss.parse(window.localStorage.getItem(STORAGE_KEY));
    if (saved === null) return;
    const px = Math.min(MAX_WIDTH_PX, Math.max(MIN_WIDTH_PX, Number.parseInt(saved, 10)));
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    wrapper?.style.setProperty("--sidebar-width", `${px}px`);
  }, []);
}

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

    const widthElements = document.querySelectorAll<HTMLElement>(
      '[data-slot="sidebar-gap"], [data-slot="sidebar-container"]',
    );
    for (const el of widthElements) {
      el.style.transition = "none";
    }
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    setDragging(true);

    let pendingX: null | number = null;
    let rafId: null | number = null;
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
      for (const el of widthElements) {
        el.style.transition = "";
      }
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
        aria-label="Resize sidebar"
        aria-orientation="vertical"
        className="hover:bg-sidebar-border/80 absolute top-0 right-0 z-20 h-full w-1 cursor-col-resize transition-colors"
        onPointerDown={handlePointerDown}
        role="separator"
      />

      {dragging ? (
        <div aria-hidden="true" className="fixed inset-0 z-50 cursor-col-resize bg-transparent" />
      ) : null}
    </>
  );
}
