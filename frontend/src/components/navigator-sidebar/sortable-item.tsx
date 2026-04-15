"use client";

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

export function SortableItem({ id, variant, children }: SortableItemProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id,
  });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const className = cn("group/navitem relative", isDragging && "z-10 opacity-60");

  const props = {
    ref: setNodeRef,
    style,
    className,
    ...attributes,
    ...listeners,
  };

  if (variant === "subitem") {
    return <SidebarMenuSubItem {...props}>{children}</SidebarMenuSubItem>;
  }
  return <SidebarMenuItem {...props}>{children}</SidebarMenuItem>;
}
