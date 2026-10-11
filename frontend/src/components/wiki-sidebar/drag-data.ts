import { z } from "zod";

const SortableDataSchema = z.object({
  containerId: z.string().optional(),
  items: z.array(z.string()).optional(),
});
const SortableContainerDataSchema = SortableDataSchema.pick({ containerId: true });
const SortablePositionDataSchema = z.object({
  containerId: z.string(),
  index: z.number().int().nonnegative(),
});
const DragScopeDataSchema = z.object({
  driveId: z.string(),
  parentFolderId: z.string().optional(),
});

type DragData = null | Record<string, unknown> | undefined;

export function sortableContainerId(data: DragData): null | string {
  const parsed = SortableContainerDataSchema.safeParse(data?.["sortable"]);
  return parsed.success ? (parsed.data.containerId ?? null) : null;
}

export function dragScopeData(data: DragData): null | z.output<typeof DragScopeDataSchema> {
  const parsed = DragScopeDataSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

export function sortablePosition(
  data: DragData,
): null | z.output<typeof SortablePositionDataSchema> {
  const parsed = SortablePositionDataSchema.safeParse(data?.["sortable"]);
  return parsed.success ? parsed.data : null;
}

export function sortableData(data: DragData): null | z.output<typeof SortableDataSchema> {
  const parsed = SortableDataSchema.safeParse(data?.["sortable"]);
  return parsed.success ? parsed.data : null;
}
