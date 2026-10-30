import { indexNodes } from "@/lib/wiki-indexes";
import type { DriveNode } from "@/types/wiki";

export function folderAncestorIds(folders: readonly DriveNode[], nodeId: string): string[] {
  const byId = indexNodes(folders);
  const chain: string[] = [];
  const seen = new Set([nodeId]);
  let current = byId.get(nodeId);
  while (current?.parentId) {
    const parentId = current.parentId;
    if (seen.has(parentId)) break;
    seen.add(parentId);
    chain.push(parentId);
    current = byId.get(parentId);
  }
  return chain.toReversed();
}
