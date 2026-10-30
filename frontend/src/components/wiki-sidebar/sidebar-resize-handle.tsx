"use client";

import { useEffect, useRef, useState } from "react";

import { SIDEBAR_WIDTH_COOKIE } from "@/lib/storage-schemas";

const MIN_WIDTH_PX = 224;
const MAX_WIDTH_PX = 576;
const KEYBOARD_STEP_PX = 16;

const clampSidebarWidth = (px: number) => Math.max(MIN_WIDTH_PX, Math.min(MAX_WIDTH_PX, px));

const resizeDirection = (sidebar: HTMLElement) =>
  sidebar.closest<HTMLElement>('[data-slot="sidebar"][data-side]')?.dataset["side"] === "right"
    ? -1
    : 1;

function intendedSidebarWidth(wrapper: HTMLElement | null, sidebar: HTMLElement) {
  const value = wrapper?.style.getPropertyValue("--sidebar-width").trim() ?? "";
  const pixels = value.endsWith("px") ? Number.parseFloat(value) : NaN;
  return Number.isFinite(pixels) && pixels > 0 ? pixels : sidebar.getBoundingClientRect().width;
}

function persistSidebarWidth(wrapper: HTMLElement) {
  const final = wrapper.style.getPropertyValue("--sidebar-width");
  if (final) {
    // eslint-disable-next-line unicorn/no-document-cookie -- SSR reads this non-sensitive preference before hydration to prevent a width jump.
    document.cookie = `${SIDEBAR_WIDTH_COOKIE}=${encodeURIComponent(final)}; Path=/; Max-Age=31536000; SameSite=Lax`;
  }
}

export function SidebarResizeHandle() {
  const [dragging, setDragging] = useState(false);
  const [width, setWidth] = useState<null | number>(null);
  const releaseDrag = useRef<(() => void) | null>(null);
  const syncWidth = (element: HTMLElement | null) => {
    if (!element) return;
    const sidebar = document.querySelector<HTMLElement>('[data-slot="sidebar-container"]');
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    setWidth(sidebar ? clampSidebarWidth(intendedSidebarWidth(wrapper, sidebar)) : null);
  };

  useEffect(() => () => releaseDrag.current?.(), []);

  const handleKeyDown = (event: KeyboardEvent) => {
    if (
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
    )
      return;
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    const sidebar = document.querySelector<HTMLElement>('[data-slot="sidebar-container"]');
    if (!wrapper || !sidebar) return;

    event.preventDefault();
    releaseDrag.current?.();
    setDragging(false);
    const current = intendedSidebarWidth(wrapper, sidebar);
    const delta = (event.key === "ArrowRight" ? 1 : -1) * resizeDirection(sidebar);
    const next =
      event.key === "Home"
        ? MIN_WIDTH_PX
        : event.key === "End"
          ? MAX_WIDTH_PX
          : clampSidebarWidth(current + delta * KEYBOARD_STEP_PX);
    wrapper.style.setProperty("--sidebar-width", `${next}px`);
    setWidth(next);
    persistSidebarWidth(wrapper);
  };

  const attachHandle = (element: HTMLDivElement | null) => {
    if (!element) return;
    syncWidth(element);
    const handleFocus = () => syncWidth(element);
    element.addEventListener("focus", handleFocus);
    element.addEventListener("keydown", handleKeyDown);
    return () => {
      element.removeEventListener("focus", handleFocus);
      element.removeEventListener("keydown", handleKeyDown);
    };
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    releaseDrag.current?.();
    const wrapper = document.querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]');
    const sidebar = document.querySelector<HTMLElement>('[data-slot="sidebar-container"]');
    if (!wrapper || !sidebar) {
      setDragging(false);
      return;
    }

    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startWidth = intendedSidebarWidth(wrapper, sidebar);
    const direction = resizeDirection(sidebar);
    setWidth(clampSidebarWidth(startWidth));
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
      const next = clampSidebarWidth(startWidth + (pendingX - startX) * direction);
      wrapper.style.setProperty("--sidebar-width", `${next}px`);
      setWidth(next);
      pendingX = null;
    };

    const onMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      pendingX = moveEvent.clientX;
      if (rafId !== null) return;
      rafId = requestAnimationFrame(apply);
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
      persistSidebarWidth(wrapper);
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
        aria-disabled={width === null || undefined}
        aria-label="Resize sidebar"
        aria-orientation="vertical"
        aria-valuemax={MAX_WIDTH_PX}
        aria-valuemin={MIN_WIDTH_PX}
        aria-valuenow={width ?? undefined}
        aria-valuetext={width === null ? undefined : `${Math.round(width)} pixels wide`}
        className="focus-visible:ring-ring hover:before:bg-sidebar-border/80 focus-visible:before:bg-sidebar-border absolute top-0 -right-1.5 z-20 hidden h-full w-3 cursor-col-resize group-data-[side=right]:right-auto group-data-[side=right]:-left-1.5 before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:transition-colors focus-visible:ring-2 focus-visible:outline-none md:block"
        onPointerDown={handlePointerDown}
        ref={attachHandle}
        role="separator"
        tabIndex={width === null ? -1 : 0}
      />

      {dragging ? (
        <div aria-hidden="true" className="fixed inset-0 z-50 cursor-col-resize bg-transparent" />
      ) : null}
    </>
  );
}
