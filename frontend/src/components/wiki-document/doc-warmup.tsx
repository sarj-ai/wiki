"use client";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import type { DriveTree } from "@/types/wiki";

import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { isGoogleWorkspaceType } from "@/lib/file-icons";
import { readLastDoc, type LastDoc } from "@/lib/last-doc";
import { wikiKeys } from "@/lib/wiki-keys";

const WARMUP_DELAY_MS = 1500;

function firstWorkspaceDoc(queryClient: QueryClient): LastDoc | null {
  const trees = queryClient.getQueriesData<DriveTree>({ queryKey: wikiKeys.driveTreePrefix });
  for (const [, tree] of trees) {
    const file = tree?.files.find((node) => isGoogleWorkspaceType(node.mimeType));
    if (file) return { fileId: file.id, mimeType: file.mimeType, title: file.name };
  }
  return null;
}

export function DocWarmup() {
  const queryClient = useQueryClient();
  const setPrewarm = useDocViewerStore((s) => s.setPrewarm);
  const hasWarmed = useRef(false);

  useEffect(() => {
    if (hasWarmed.current) return;
    const timer = window.setTimeout(() => {
      if (useDocViewerStore.getState().active) return;
      hasWarmed.current = true;
      const target = readLastDoc() ?? firstWorkspaceDoc(queryClient);
      if (target) setPrewarm({ ...target, reloadKey: 0 });
    }, WARMUP_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [queryClient, setPrewarm]);

  return null;
}
