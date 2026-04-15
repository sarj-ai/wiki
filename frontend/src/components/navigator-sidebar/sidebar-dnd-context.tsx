"use client";

import { DndContext, PointerSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { arrayMove } from "@dnd-kit/sortable";
import { toast } from "sonner";
import { useBulkSetSortOrders } from "@/hooks/use-navigator-mutations";
import { useSortOrders } from "@/hooks/use-navigator-tree";
import { fractionalIndexFor } from "@/lib/fractional-index";

// The shape we read from SortableContext item ids. Each sortable node
// registers with its drive file id directly — no prefix — so the
// "containerId" is what differentiates two items in the same parent
// from two items in different parents.

export function SidebarDndContext({ children }: { children: React.ReactNode }) {
  const sortOrdersQuery = useSortOrders();
  const bulkSetSortOrders = useBulkSetSortOrders();

  // Drag doesn't kick off until the pointer moves 5px — a single click
  // on an item still goes through to the underlying onClick handlers
  // (folder expand, file navigation, action menus).
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const activeContainerId = active.data.current?.sortable?.containerId as string | undefined;
    const overContainerId = over.data.current?.sortable?.containerId as string | undefined;
    if (!activeContainerId || !overContainerId) return;

    // Cross-container drops are intentionally ignored here — moving a
    // file to another folder goes through the "Move to..." dialog in
    // the item menu so both paths share one mutation site.
    if (activeContainerId !== overContainerId) return;

    const items = (active.data.current?.sortable?.items ?? []) as string[];
    const oldIndex = items.indexOf(String(active.id));
    const newIndex = items.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;

    const reordered = arrayMove(items, oldIndex, newIndex);
    const sortOrderMap = new Map<string, number>();
    for (const s of sortOrdersQuery.data ?? []) sortOrderMap.set(s.driveId, s.sortOrder);

    const newSortOrder = fractionalIndexFor(reordered, newIndex, String(active.id), (id) =>
      sortOrderMap.get(id),
    );

    try {
      await bulkSetSortOrders.mutateAsync([
        { driveId: String(active.id), sortOrder: newSortOrder },
      ]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Reorder failed";
      toast.error(message);
    }
  };

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      {children}
    </DndContext>
  );
}
