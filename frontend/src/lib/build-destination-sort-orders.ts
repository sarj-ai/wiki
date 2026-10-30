import { EMPTY_NODES, indexChildren, indexSidebarNodes, indexSortOrders } from "@/lib/wiki-indexes";
import { buildSiblingItems, orderSiblingItems } from "@/lib/wiki-tree";
import type { DriveNode, DriveTree, SortOrder } from "@/types/wiki";

interface DestinationOrderArgs {
  driveId: string;
  folderFiles: readonly DriveNode[];
  folderId: string;
  movedNode: DriveNode;
  positionHint: { rowId: string; intent: "above" | "below" };
  sortOrders: readonly SortOrder[];
  tree: DriveTree | undefined;
}

export function buildDestinationSortOrders({
  driveId,
  folderFiles,
  folderId,
  movedNode,
  positionHint,
  sortOrders,
  tree,
}: DestinationOrderArgs): null | SortOrder[] {
  const parentKey = folderId === driveId ? "__root__" : folderId;
  const folders = indexChildren(tree?.folders ?? EMPTY_NODES).get(parentKey) ?? EMPTY_NODES;
  const files = tree?.filesComplete
    ? (indexChildren(tree.files).get(parentKey) ?? EMPTY_NODES)
    : indexSidebarNodes(folderFiles).files;
  const items = buildSiblingItems(folders, files).filter((item) => item.node.id !== movedNode.id);
  const baseline = orderSiblingItems(items, indexSortOrders(sortOrders), null);
  const overIndex = baseline.findIndex((item) => item.node.id === positionHint.rowId);
  if (overIndex < 0) return null;
  const insertIndex = positionHint.intent === "below" ? overIndex + 1 : overIndex;
  const finalOrder: SortOrder[] = [];
  let index = 0;
  for (const item of baseline) {
    if (index === insertIndex) {
      finalOrder.push({ driveId: movedNode.id, sortOrder: index + 1 });
    }
    finalOrder.push({
      driveId: item.node.id,
      sortOrder: index < insertIndex ? index + 1 : index + 2,
    });
    index++;
  }
  if (insertIndex === baseline.length) {
    finalOrder.push({ driveId: movedNode.id, sortOrder: baseline.length + 1 });
  }
  return finalOrder;
}
