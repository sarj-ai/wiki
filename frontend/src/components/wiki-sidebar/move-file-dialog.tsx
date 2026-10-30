"use client";

import { ChevronRight, Folder, HardDrive, Loader2 } from "lucide-react";
import { memo, useRef, useState } from "react";
import { toast } from "sonner";
import { useStore } from "zustand";
import { createStore, type StoreApi } from "zustand/vanilla";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useMoveFile } from "@/hooks/use-wiki-mutations";
import { useDrives, useDriveTree } from "@/hooks/use-wiki-tree";
import { ApiError } from "@/lib/api-error";
import { runWithErrorToast, toErrorMessage } from "@/lib/mutation-toast";
import { cn } from "@/lib/utils";
import { EMPTY_NODES, indexDestinationFolders } from "@/lib/wiki-indexes";
import type { DriveNode } from "@/types/wiki";

import { DriveListFeedback } from "./drive-list-feedback";
import { rowIndent } from "./layout-constants";

export function MoveFileDialog({
  currentDriveId,
  currentParentFolderId,
  fileId,
  fileName,
  onCloseAutoFocus,
  onOpenAutoFocus,
  onOpenChange,
  open,
}: MoveFileDialogProps) {
  const drivesQuery = useDrives();
  const hasNoDestinations = drivesQuery.isSuccess && drivesQuery.data.length === 0;
  const moveFile = useMoveFile();
  const [selection] = useState(() =>
    createStore<DestinationSelection>((set) => ({
      select: (selectedId) => set({ selectedId }),
      selectedId: null,
    })),
  );
  const selectedId = useStore(selection, (state) => state.selectedId);
  const admission = useRef({ pending: false });

  const handleConfirm = async () => {
    const owner = admission.current;
    if (!selectedId || owner.pending) return;
    owner.pending = true;
    try {
      await runWithErrorToast(
        async () => {
          await moveFile.mutateAsync({
            fileId,
            newParentFolderId: selectedId,
            sourceDriveId: currentDriveId,
            sourceParentFolderId: currentParentFolderId,
          });
          toast.success(`Moved "${fileName}"`);
          onOpenChange(false);
          selection.getState().select(null);
        },

        (err) =>
          err instanceof ApiError && err.status === 403
            ? "Drive denied this move — you may not have edit permission on the destination."
            : toErrorMessage(err, "Move failed"),
      );
    } finally {
      owner.pending = false;
    }
  };

  const disabledId = currentParentFolderId ?? currentDriveId ?? "";

  return (
    <Dialog
      onOpenChange={(next) => {
        if (moveFile.isPending) return;
        onOpenChange(next);
      }}
      open={open}
    >
      <DialogContent
        className="max-h-[calc(100dvh-1rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-md"
        onCloseAutoFocus={onCloseAutoFocus}
        onOpenAutoFocus={onOpenAutoFocus}
      >
        <DialogHeader className="px-6 pt-6 pr-12">
          <DialogTitle>Move &ldquo;{fileName}&rdquo;</DialogTitle>
          <DialogDescription>
            Pick a destination folder or drive. Cross-drive moves may be blocked by Drive admin
            settings — copy the file instead if that fails.
          </DialogDescription>
        </DialogHeader>
        <div
          aria-label="Move destinations"
          className="mx-6 my-4 max-h-72 min-h-0 overflow-y-auto rounded-sm border"
          role="region"
          tabIndex={0}
        >
          <div className="space-y-0.5 p-1">
            <DriveListFeedback
              empty={hasNoDestinations}
              emptyMessage="No available destinations."
              query={drivesQuery}
            />
            {(drivesQuery.data ?? []).map((drive) => (
              <DriveRow
                disabledId={disabledId}
                driveId={drive.id}
                driveName={drive.name}
                key={drive.id}
                selection={selection}
              />
            ))}
          </div>
        </div>
        <DialogFooter className="border-t px-6 py-4">
          <Button
            disabled={moveFile.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
            variant="outline"
          >
            Cancel
          </Button>
          <Button
            disabled={!selectedId || moveFile.isPending}
            onClick={() => {
              void handleConfirm().catch(() => console.warn("Move confirmation could not finish"));
            }}
          >
            {moveFile.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Moving…
              </>
            ) : (
              "Move"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface MoveFileDialogProps {
  currentDriveId: string | undefined;
  currentParentFolderId: string | undefined;
  fileId: string;
  fileName: string;
  onCloseAutoFocus?: (event: Event) => void;
  onOpenAutoFocus?: (event: Event) => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}

interface DestinationSelection {
  select: (selectedId: null | string) => void;
  selectedId: null | string;
}

interface FolderRowProps {
  childrenByParent: Map<string, DriveNode[]>;
  depth: number;
  disabledId: string;
  folder: DriveNode;
  selection: StoreApi<DestinationSelection>;
}

interface DriveRowProps {
  disabledId: string;
  driveId: string;
  driveName: string;
  selection: StoreApi<DestinationSelection>;
}

const FolderRow = memo(function FolderRowContent({
  childrenByParent,
  depth,
  disabledId,
  folder,
  selection,
}: FolderRowProps) {
  const selected = useStore(selection, (state) => state.selectedId === folder.id);
  const [expanded, setExpanded] = useState(false);
  const subFolders = childrenByParent.get(folder.id) ?? EMPTY_NODES;
  const isDisabled = folder.id === disabledId;

  return (
    <>
      <div className="flex items-center gap-1" style={{ paddingLeft: `${rowIndent(depth)}px` }}>
        <Button
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${folder.name}`}
          className="hover:bg-muted size-6 shrink-0 rounded-sm"
          disabled={subFolders.length === 0}
          onClick={() => {
            setExpanded((v) => !v);
          }}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          {subFolders.length > 0 && (
            <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
          )}
        </Button>
        <Button
          className={cn(
            "h-7 min-w-0 flex-1 justify-start gap-1 px-2 font-normal",
            selected && "bg-accent",
            isDisabled && "opacity-40",
          )}
          disabled={isDisabled}
          onClick={() => {
            selection.getState().select(folder.id);
          }}
          size="sm"
          type="button"
          variant="ghost"
        >
          <Folder className="size-4 shrink-0" />
          <span className="truncate">{folder.name}</span>
        </Button>
      </div>
      {expanded
        ? subFolders.map((child) => (
            <FolderRow
              childrenByParent={childrenByParent}
              depth={depth + 1}
              disabledId={disabledId}
              folder={child}
              key={child.id}
              selection={selection}
            />
          ))
        : null}
    </>
  );
});

const DriveRow = memo(function DriveRowContent({
  disabledId,
  driveId,
  driveName,
  selection,
}: DriveRowProps) {
  const selected = useStore(selection, (state) => state.selectedId === driveId);
  const [expanded, setExpanded] = useState(false);

  const treeQuery = useDriveTree(expanded ? driveId : null);

  const childrenByParent = indexDestinationFolders(treeQuery.data?.folders ?? EMPTY_NODES);
  const rootFolders = childrenByParent.get("__root__") ?? EMPTY_NODES;
  const isDriveDisabled = driveId === disabledId;

  return (
    <>
      <div className="flex items-center gap-1">
        <Button
          aria-expanded={expanded}
          aria-label={`${expanded ? "Collapse" : "Expand"} ${driveName}`}
          className="hover:bg-muted size-6 shrink-0 rounded-sm"
          onClick={() => {
            setExpanded((v) => !v);
          }}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
        </Button>
        <Button
          className={cn(
            "h-7 min-w-0 flex-1 justify-start gap-1 px-2",
            selected && !isDriveDisabled && "bg-accent",
            isDriveDisabled && "opacity-60",
          )}
          disabled={isDriveDisabled}
          onClick={() => {
            selection.getState().select(driveId);
          }}
          size="sm"
          type="button"
          variant="ghost"
        >
          <HardDrive className="size-4 shrink-0" />
          <span className="truncate">{driveName}</span>
        </Button>
      </div>
      {expanded && treeQuery.isLoading ? (
        <div
          className="text-muted-foreground flex items-center gap-2 px-4 py-1 text-xs"
          role="status"
        >
          <Loader2 className="size-3 animate-spin" />
          Loading folders…
        </div>
      ) : null}
      {expanded && treeQuery.isError ? (
        <div className="space-y-2 px-4 py-2 text-xs" role="alert">
          <p>Folders could not be loaded.</p>
          <Button
            disabled={treeQuery.isFetching}
            onClick={() => {
              void treeQuery.refetch().catch(() => console.warn("Folder retry could not finish"));
            }}
            size="sm"
            type="button"
            variant="outline"
          >
            Retry folders
          </Button>
        </div>
      ) : null}
      {expanded && !treeQuery.isLoading
        ? rootFolders.map((folder) => (
            <FolderRow
              childrenByParent={childrenByParent}
              depth={1}
              disabledId={disabledId}
              folder={folder}
              key={folder.id}
              selection={selection}
            />
          ))
        : null}
    </>
  );
});
