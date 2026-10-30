"use client";

import { useDroppable } from "@dnd-kit/core";

import { SidebarMenuItem, SidebarMenuSubItem } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

export function EmptyDropZone({ driveId, label, parentId, variant }: EmptyDropZoneProps) {
  const containerId = `parent:${parentId}`;
  const { isOver, setNodeRef } = useDroppable({
    data: { driveId, parentFolderId: parentId === driveId ? undefined : parentId },
    id: containerId,
  });

  const className = cn(
    "transition-[background-color,box-shadow] duration-150",
    isOver && "bg-primary/10 ring-1 ring-primary/60 rounded-md",
  );

  if (variant === "subitem") {
    return (
      <SidebarMenuSubItem className={className} ref={setNodeRef}>
        <span className="text-muted-foreground px-2 py-1 text-xs">{label}</span>
      </SidebarMenuSubItem>
    );
  }
  return (
    <SidebarMenuItem className={className} ref={setNodeRef}>
      <span className="text-muted-foreground px-2 py-1 text-xs">{label}</span>
    </SidebarMenuItem>
  );
}

interface EmptyDropZoneProps {
  driveId: string;
  label: string;
  parentId: string;
  variant: "item" | "subitem";
}
