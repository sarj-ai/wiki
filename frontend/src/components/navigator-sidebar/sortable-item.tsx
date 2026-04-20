"use client";

import { useRef } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { SidebarMenuItem, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

interface SortableItemProps {
  id: string;
  // Whether to render as a top-level SidebarMenuItem (inside a
  // SidebarMenu) or as a nested SidebarMenuSubItem (inside a
  // SidebarMenuSub). The dnd bits are identical.
  variant: "item" | "subitem";
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

export function SortableItem({ id, variant, children }: SortableItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
    animateLayoutChanges: NO_LAYOUT_ANIMATION,
  });

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

  if (variant === "subitem") {
    return <SidebarMenuSubItem {...props}>{children}</SidebarMenuSubItem>;
  }
  return <SidebarMenuItem {...props}>{children}</SidebarMenuItem>;
}
