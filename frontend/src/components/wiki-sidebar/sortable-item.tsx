"use client";

import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useRef } from "react";

import { SidebarMenuItem, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

import { useDropEdgeForRow } from "./use-drag-over-store";

interface SortableItemProps {
  id: string;

  variant: "item" | "subitem";

  data?: Record<string, unknown>;
  children: React.ReactNode;
}

const DRAG_CLICK_SUPPRESS_PX = 5;

const NO_LAYOUT_ANIMATION = () => false;

export function SortableItem({ id, variant, data = {}, children }: SortableItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
    data,
    animateLayoutChanges: NO_LAYOUT_ANIMATION,
  });

  const dropEdge = useDropEdgeForRow(id);

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const className = cn("group/navitem relative", isDragging && "invisible");

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
