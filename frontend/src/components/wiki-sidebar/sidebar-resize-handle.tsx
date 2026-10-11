"use client";

import { useEffect, useRef, useState } from "react";

import { SIDEBAR_WIDTH_COOKIE } from "@/lib/storage-schemas";

const MIN_WIDTH_PX = 224;
const MAX_WIDTH_PX = 576;

const clampSidebarWidth = (px: number) => Math.max(MIN_WIDTH_PX, Math.min(MAX_WIDTH_PX, px));

export function SidebarResizeHandle() {
  const [dragging, setDragging] = useState(false);
  const releaseDrag = useRef<(() => void) | null>(null);

  useEffect(() => () => releaseDrag.current?.(), []);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    releaseDrag.current?.();
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    const sidebar = document.querySelector<HTMLElement>('[data-slot="sidebar-inner"]');
    if (!wrapper || !sidebar) {
      setDragging(false);
      return;
    }

    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startWidth = sidebar.getBoundingClientRect().width;
    const widthElements = Array.from(
      document.querySelectorAll<HTMLElement>(
        '[data-slot="sidebar-gap"], [data-slot="sidebar-container"]',
      ),
      (element) => ({ element, transition: element.style.transition }),
    );
    for (const { element: el } of widthElements) {
      el.style.transition = "none";
    }
    const previousUserSelect = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    setDragging(true);

    let pendingX: null | number = null;
    let rafId: null | number = null;
    let released = false;
    const apply = () => {
      rafId = null;
      if (released || pendingX === null) return;
      const next = clampSidebarWidth(startWidth + (pendingX - startX));
      wrapper.style.setProperty("--sidebar-width", `${next}px`);
      pendingX = null;
    };

    const onMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      pendingX = moveEvent.clientX;
      rafId ??= requestAnimationFrame(apply);
    };

    const release = () => {
      if (released) return;
      released = true;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("blur", onBlur);
      if (rafId !== null) cancelAnimationFrame(rafId);
      rafId = null;
      pendingX = null;
      document.body.style.userSelect = previousUserSelect;
      document.body.style.cursor = previousCursor;
      for (const { element: el, transition } of widthElements) {
        el.style.transition = transition;
      }
      releaseDrag.current = null;
    };

    const onUp = (upEvent: PointerEvent) => {
      if (upEvent.pointerId !== pointerId) return;
      if (rafId !== null) cancelAnimationFrame(rafId);
      pendingX = upEvent.clientX;
      apply();
      release();
      const final = wrapper.style.getPropertyValue("--sidebar-width");
      if (final) {
        // eslint-disable-next-line unicorn/no-document-cookie -- SSR reads this non-sensitive preference before hydration to prevent a width jump.
        document.cookie = `${SIDEBAR_WIDTH_COOKIE}=${encodeURIComponent(final)}; Path=/; Max-Age=31536000; SameSite=Lax`;
      }
      setDragging(false);
    };

    const onCancel = (cancelEvent: PointerEvent) => {
      if (cancelEvent.pointerId !== pointerId) return;
      onBlur();
    };
    const onBlur = () => {
      release();
      setDragging(false);
    };

    releaseDrag.current = release;
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("blur", onBlur);
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
