"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useRef } from "react";

import { SidebarMenuItem, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

import { useDropEdgeForRow } from "./use-drag-over-store";

interface SortableItemProps {
  id: string;
  // Whether to render as a top-level SidebarMenuItem (inside a
  // SidebarMenu) or as a nested SidebarMenuSubItem (inside a
  // SidebarMenuSub). The dnd bits are identical.
  variant: "item" | "subitem";
  // Custom data merged into the sortable's data payload — read in
  // ``onDragOver`` to decide whether the over row is folder-eligible
  // for an "into" drop intent.
  data?: Record<string, unknown>;
  children: React.ReactNode;
}

// Match dnd-kit's PointerSensor activation distance. A pointer that
// moved less than this between pointerdown and click is a genuine
// click; beyond it was a drag and the trailing click must be swallowed.
const DRAG_CLICK_SUPPRESS_PX = 5;

// On drop, dnd-kit would otherwise run a FLIP "layout change" animation
// on every item whose drag-time predicted index doesn't line up with
// its current DOM index. The dragged item (and its neighbours) ends up
// visually snapped to its pre-drop rect, then slides to the new rect
// over ~200ms — exactly the "goes back, then animates forward" bounce
// users were seeing. We reorder synchronously via our optimistic
// setState and React's own DOM reconciliation lands each item in the
// right slot immediately, so disabling the layout-change animation
// does not leave anything unanimated; the during-drag displacement
// still runs because that path keys off ``isSorting``, not this flag.
const NO_LAYOUT_ANIMATION = () => false;

export function SortableItem({ id, variant, data = {}, children }: SortableItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
    data,
    animateLayoutChanges: NO_LAYOUT_ANIMATION,
  });

  // Drop-edge indicator: a thin horizontal line above or below the
  // row showing exactly where the dragged item will land for an
  // "above" / "below" intent. The signal is computed by the reorder
  // hook from cursor Y on the over rect; we just render the line
  // here so it lives next to the row's bounding box.
  const dropEdge = useDropEdgeForRow(id);

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  // While a DragOverlay portal renders the dragged copy following the
  // cursor, the source row hides entirely. ``invisible`` preserves the
  // row's box so the list doesn't collapse; it paints nothing, so no
  // descendant transition can fire from a transform-clearing reflow on
  // drop — the source row simply becomes visible again at the new DOM
  // slot after React's reconciliation lands it there.
  const className = cn("group/navitem relative", isDragging && "invisible");

  // After a drag release, the browser still fires a synthetic ``click``
  // on whatever element sits under the pointer. The inner Next.js
  // ``<Link>`` interprets that as a navigation request — which is what
  // users saw as the sidebar "refreshing" back to the dragged file.
  // We track the pointerdown origin in capture phase and, on click,
  // swallow the event if the pointer travelled more than dnd-kit's
  // activation threshold. Capture phase runs before the Link's own
  // handler, so the navigation is blocked before Next.js sees it.
  const pointerOrigin = useRef<{ x: number; y: number } | null>(null);
  const handlePointerDownCapture = (e: React.PointerEvent<HTMLLIElement>) => {
    pointerOrigin.current = { x: e.clientX, y: e.clientY };
  };
  const handleClickCapture = (e: React.MouseEvent<HTMLLIElement>) => {
    const origin = pointerOrigin.current;
    pointerOrigin.current = null;
    if (!origin) return;
    const dx = Math.abs(e.clientX - origin.x);
    const dy = Math.abs(e.clientY - origin.y);
    if (dx > DRAG_CLICK_SUPPRESS_PX || dy > DRAG_CLICK_SUPPRESS_PX) {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  const props = {
    ref: setNodeRef,
    style,
    className,
    onPointerDownCapture: handlePointerDownCapture,
    onClickCapture: handleClickCapture,
    ...attributes,
    ...listeners,
  };

  const dropIndicator = dropEdge ? (
    // Rounded box sitting in the gap above or below the row to mark
    // the drop slot. Tailored to read as "the dragged row will land
    // here" — primary tint + ring, sized to fit the SidebarMenu's
    // gap between rows so it reads as a slot, not a row decoration.
    // Pointer-events-none so the indicator never steals a drop from
    // the row beneath.
    <div
      aria-hidden
      className={cn(
        "bg-primary/30 ring-primary pointer-events-none absolute right-1 left-1 z-30 h-1.5 rounded-full ring-2",
        dropEdge === "above" ? "-top-1" : "-bottom-1",
      )}
    />
  ) : null;

  if (variant === "subitem") {
    return (
      <SidebarMenuSubItem {...props}>
        {dropIndicator}
        {children}
      </SidebarMenuSubItem>
    );
  }
  return (
    <SidebarMenuItem {...props}>
      {dropIndicator}
      {children}
    </SidebarMenuItem>
  );
}
