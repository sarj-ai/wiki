"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { navigatorApi } from "@/lib/navigator-api";
import { useDrives, useDriveTree } from "@/hooks/use-navigator-tree";
import { FileViewer } from "@/components/navigator-document/file-viewer";
import { NavigatorToolbar } from "@/components/navigator-document/navigator-toolbar";
import type { DriveNode, FileResponse } from "@/types/navigator";
import { MIME_FOLDER } from "@/types/navigator";

function useFileMetadata(fileId: string | null) {
  return useQuery<FileResponse>({
    queryKey: fileId ? ["navigator", "file", fileId] : ["navigator", "file", "disabled"],
    queryFn: () => navigatorApi.getFile(fileId ?? ""),
    enabled: !!fileId,
  });
}

function buildBreadcrumb(
  driveName: string | null,
  folders: DriveNode[],
  parentFolderId: string | null,
): string[] {
  // Walk up the folder chain from the file's immediate parent. The
  // walker returns folders keyed by their own id, so we need a lookup
  // map to chain parent ids into a path.
  const byId = new Map<string, DriveNode>();
  for (const folder of folders) {
    if (folder.mimeType === MIME_FOLDER) byId.set(folder.id, folder);
  }
  const chain: string[] = [];
  let current = parentFolderId;
  // Safety cap — folder nesting over 64 levels is pathological and
  // almost certainly a cycle we shouldn't walk.
  let safety = 64;
  while (current && safety > 0) {
    const folder = byId.get(current);
    if (!folder) break;
    chain.unshift(folder.name);
    current = folder.parentId;
    safety--;
  }
  if (driveName) chain.unshift(driveName);
  return chain;
}

export default function FilePage() {
  const params = useParams<{ driveId: string }>();
  const fileId = params.driveId ?? null;
  const fileQuery = useFileMetadata(fileId);
  const drivesQuery = useDrives();
  const driveTreeQuery = useDriveTree(fileQuery.data?.driveId ?? null);

  const driveName = useMemo(() => {
    const driveId = fileQuery.data?.driveId;
    if (!driveId) return null;
    return drivesQuery.data?.find((d) => d.id === driveId)?.name ?? null;
  }, [fileQuery.data?.driveId, drivesQuery.data]);

  const breadcrumb = useMemo(() => {
    if (!fileQuery.data) return [];
    return buildBreadcrumb(
      driveName,
      driveTreeQuery.data?.folders ?? [],
      fileQuery.data.parentFolderId,
    );
  }, [fileQuery.data, driveName, driveTreeQuery.data]);

  if (fileQuery.isLoading) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        Loading file…
      </div>
    );
  }

  if (fileQuery.isError || !fileQuery.data) {
    return (
      <div className="text-destructive flex h-full items-center justify-center text-sm">
        Failed to load this file. Check that you have access in Google Drive.
      </div>
    );
  }

  const file = fileQuery.data;

  return (
    <div className="flex h-[100dvh] flex-col">
      <NavigatorToolbar
        fileId={file.id}
        mimeType={file.mimeType}
        title={file.name}
        breadcrumb={breadcrumb}
      />
      <div className="flex-1 overflow-hidden">
        <FileViewer fileId={file.id} mimeType={file.mimeType} title={file.name} />
      </div>
    </div>
  );
}
