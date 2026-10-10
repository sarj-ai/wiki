import { test } from "vitest";

import { indexChildren } from "@/lib/wiki-indexes";
import { buildChildrenIndex } from "@/lib/wiki-tree";
import type { DriveNode } from "@/types/wiki";
import { MIME_DOC } from "@/types/wiki";

const NODES: DriveNode[] = Array.from({ length: 10_000 }, (_, index) => ({
  id: `file-${index}`,
  name: `Document ${index}`,
  mimeType: MIME_DOC,
  parentId: `folder-${index % 100}`,
  modifiedTime: undefined,
  shortcutInaccessible: false,
  shortcutTargetDriveId: undefined,
  shortcutTargetDriveName: undefined,
  shortcutTargetId: undefined,
  shortcutTargetMimeType: undefined,
}));
indexChildren(NODES);

test("10,000-node sidebar index", async ({ bench }) => {
  const results = await bench.compare(
    bench("rebuild index on each render", () => {
      buildChildrenIndex(NODES);
    }),
    bench("reuse shared immutable index", () => {
      indexChildren(NODES);
    }),
    { time: 300 },
  );
  process.stdout.write(
    `${JSON.stringify({
      rebuildMedianMs: results.get("rebuild index on each render").latency.p50,
      reuseMedianMs: results.get("reuse shared immutable index").latency.p50,
    })}\n`,
  );
});
