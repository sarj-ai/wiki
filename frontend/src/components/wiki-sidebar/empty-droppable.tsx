"use client";

import { useDroppable } from "@dnd-kit/core";

import { SidebarMenuItem, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

/**
 * Registers an empty container (drive root or folder) as a drop
 * target so users can drag items INTO it from elsewhere even when
 * there are no rows to land near. Without this, dnd-kit's collision
 * detection picks the closest sortable row — which is always
 * something INSIDE the source container — and the drop reads as a
 * same-container reorder rather than a cross-container move.
 *
 * The droppable id matches the SortableContext id convention
 * (``parent:<drive-or-folder-id>``) so onDragEnd can extract the
 * destination's parent id with the same ``parentIdOf`` helper used
 * for normal sortable drops.
 */
interface EmptyDropZoneProps {
  parentId: string;
  variant: "item" | "subitem";
  label: string;
}

export function EmptyDropZone({ parentId, variant, label }: EmptyDropZoneProps) {
  const containerId = `parent:${parentId}`;
  const { setNodeRef, isOver } = useDroppable({ id: containerId });

  const className = cn(
    "transition-[background-color,box-shadow] duration-150",
    isOver && "bg-primary/10 ring-1 ring-primary/60 rounded-md",
  );

  if (variant === "subitem") {
    return (
      <SidebarMenuSubItem ref={setNodeRef} className={className}>
        <span className="text-muted-foreground px-2 py-1 text-xs">{label}</span>
      </SidebarMenuSubItem>
    );
  }
  return (
    <SidebarMenuItem ref={setNodeRef} className={className}>
      <span className="text-muted-foreground px-2 py-1 text-xs">{label}</span>
    </SidebarMenuItem>
  );
}
