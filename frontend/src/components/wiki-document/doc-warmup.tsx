"use client";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import type { DriveTree } from "@/types/wiki";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { isGoogleWorkspaceType } from "@/lib/file-icons";
import { readRecentDocs, type LastDoc } from "@/lib/last-doc";
import { wikiKeys } from "@/lib/wiki-keys";

const WARMUP_DELAY_MS = 1500;

const WARM_COUNT = 3;

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

export function DocWarmup() {
  const queryClient = useQueryClient();
  const setWarm = useDocViewerStore((s) => s.setWarm);
  const hasWarmed = useRef(false);

  useEffect(() => {
    if (hasWarmed.current) return;
    const timer = window.setTimeout(() => {
      if (useDocViewerStore.getState().active) return;
      hasWarmed.current = true;
      const recents = readRecentDocs();
      const targets =
        recents.length > 0
          ? recents.slice(0, WARM_COUNT)
          : firstWorkspaceDocs(queryClient, WARM_COUNT);
      if (targets.length > 0) {
        setWarm(targets.map((doc) => ({ ...doc, reloadKey: 0 })));
      }
    }, WARMUP_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [queryClient, setWarm]);

  return null;
}
