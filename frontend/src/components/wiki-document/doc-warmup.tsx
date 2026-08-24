"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { fileQueryOptions } from "@/hooks/use-wiki-tree";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { isGoogleWorkspaceType } from "@/lib/file-icons";
import { type LastDoc, readRecentDocs } from "@/lib/last-doc";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveTree } from "@/types/wiki";

import { readNavigatorHints, warmCountFor } from "./doc-warmup-policy";

export function DocWarmup() {
  const queryClient = useQueryClient();
  const userId = useBrowserUserId();
  const setWarm = useDocViewerStore((s) => s.setWarm);
  const hasWarmed = useRef(false);

  useEffect(() => {
    if (hasWarmed.current) return;
    const run = () => {
      if (document.visibilityState !== "visible") return;
      if (useDocViewerStore.getState().active) return;
      hasWarmed.current = true;
      const limit = warmCountFor(readNavigatorHints(navigator));
      if (limit === 0) return;
      const recents = userId ? readRecentDocs(userId) : [];
      const targets =
        recents.length > 0 ? recents.slice(0, limit) : firstWorkspaceDocs(queryClient, limit);
      if (targets.length > 0) void authorizeAndWarm(queryClient, targets, setWarm);
    };
    return scheduleWarmup(run);
  }, [queryClient, setWarm, userId]);

  return null;
}

async function authorizeAndWarm(
  queryClient: QueryClient,
  targets: LastDoc[],
  setWarm: ReturnType<typeof useDocViewerStore.getState>["setWarm"],
): Promise<void> {
  const settled = await Promise.allSettled(
    targets.map(async (target) => await queryClient.fetchQuery(fileQueryOptions(target.fileId))),
  );
  setWarm(
    settled.flatMap((result) =>
      result.status === "fulfilled"
        ? [
            {
              fileId: result.value.id,
              mimeType: result.value.mimeType,
              reloadKey: 0,
              title: result.value.name,
              ...(result.value.modifiedTime === undefined
                ? {}
                : { version: result.value.modifiedTime }),
            },
          ]
        : [],
    ),
  );
}

function scheduleWarmup(run: () => void): () => void {
  const timer = window.setTimeout(run, 3000);
  return () => {
    window.clearTimeout(timer);
  };
}

function firstWorkspaceDocs(queryClient: QueryClient, limit: number): LastDoc[] {
  const trees = queryClient.getQueriesData<DriveTree>({ queryKey: wikiKeys.driveTreePrefix });
  const docs: LastDoc[] = [];
  for (const [, tree] of trees) {
    for (const node of tree?.files ?? []) {
      if (!isGoogleWorkspaceType(node.mimeType)) continue;
      docs.push({ fileId: node.id, mimeType: node.mimeType, title: node.name });
      if (docs.length >= limit) return docs;
    }
  }
  return docs;
}
