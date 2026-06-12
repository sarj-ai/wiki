"use client";

import { ChevronRight, Folder, HardDrive, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { DriveNode } from "@/types/wiki";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useMoveFile } from "@/hooks/use-wiki-mutations";
import { useDrives, useDriveTree } from "@/hooks/use-wiki-tree";
import { ApiError } from "@/lib/api-client";
import { runWithErrorToast, toErrorMessage } from "@/lib/mutation-toast";
import { cn } from "@/lib/utils";
import { buildChildrenIndex } from "@/lib/wiki-tree";
import { MIME_SHORTCUT } from "@/types/wiki";

import { rowIndent } from "./layout-constants";

interface MoveFileDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fileId: string;
  fileName: string;
  currentDriveId: string | undefined;
  currentParentFolderId: string | undefined;
}

interface FolderRowProps {
  folder: DriveNode;
  childrenByParent: Map<string, DriveNode[]>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  disabledId: string;
  depth: number;
}

interface DriveRowProps {
  driveId: string;
  driveName: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  disabledId: string;
}

function FolderRow({
  folder,
  childrenByParent,
  selectedId,
  onSelect,
  disabledId,
  depth,
}: FolderRowProps) {
  const [expanded, setExpanded] = useState(false);
  const subFolders = childrenByParent.get(folder.id) ?? [];
  const isDisabled = folder.id === disabledId;

  return (
    <>
      <div className="flex items-center gap-1" style={{ paddingLeft: `${rowIndent(depth)}px` }}>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          onClick={() => {
            setExpanded((v) => !v);
          }}
          className="hover:bg-muted size-4 shrink-0 rounded"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {subFolders.length > 0 && (
            <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={isDisabled}
          onClick={() => {
            onSelect(folder.id);
          }}
          className={cn(
            "h-7 flex-1 justify-start gap-1 px-2 font-normal",
            selectedId === folder.id && "bg-accent",
            isDisabled && "opacity-40",
          )}
        >
          <Folder className="size-4 shrink-0" />
          <span className="truncate">{folder.name}</span>
        </Button>
      </div>
      {expanded &&
        subFolders.map((child) => (
          <FolderRow
            key={child.id}
            folder={child}
            childrenByParent={childrenByParent}
            selectedId={selectedId}
            onSelect={onSelect}
            disabledId={disabledId}
            depth={depth + 1}
          />
        ))}
    </>
  );
}

// Subscribes to the drive-tree query for THIS drive. If the sidebar
// already expanded the drive earlier in the session (or persisted it
// to localStorage), the data is served instantly from cache. If not,
// the dialog kicks off a fresh fetch — the user gets a brief loading
// state instead of an empty list, which the previous qc.getQueryData
// approach silently produced.
function DriveRow({ driveId, driveName, selectedId, onSelect, disabledId }: DriveRowProps) {
  const [expanded, setExpanded] = useState(false);
  // Only fetch once the user expands this drive — otherwise opening
  // the dialog kicks N drive-tree walks for every shared drive.
  const treeQuery = useDriveTree(expanded ? driveId : null);
  // Folder shortcuts can't be move destinations — Drive rejects "move
  // file into a shortcut" because the shortcut isn't a real container.
  // Strip them from the destination tree so users can't pick something
  // that would silently fail at submit time.
  const childrenByParent = useMemo(
    () =>
      buildChildrenIndex(
        (treeQuery.data?.folders ?? []).filter((n) => n.mimeType !== MIME_SHORTCUT),
      ),
    [treeQuery.data],
  );
  const rootFolders = childrenByParent.get("__root__") ?? [];
  const isDriveDisabled = driveId === disabledId;

  return (
    <>
      {/*
        Disabled drives stay expandable so users can navigate to
        subfolders inside them — even when the drive ROOT itself
        can't be the destination (because it's the file's current
        parent). The chevron Button stays enabled while the
        selection Button takes the disabled state.
      */}
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          onClick={() => {
            setExpanded((v) => !v);
          }}
          className="hover:bg-muted size-4 shrink-0 rounded"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={isDriveDisabled}
          onClick={() => {
            onSelect(driveId);
          }}
          className={cn(
            "h-7 flex-1 justify-start gap-1 px-2",
            selectedId === driveId && !isDriveDisabled && "bg-accent",
            isDriveDisabled && "opacity-60",
          )}
        >
          <HardDrive className="size-4 shrink-0" />
          <span className="truncate">{driveName}</span>
        </Button>
      </div>
      {expanded && treeQuery.isLoading && (
        <div className="text-muted-foreground flex items-center gap-2 px-4 py-1 text-xs">
          <Loader2 className="size-3 animate-spin" />
          Loading folders…
        </div>
      )}
      {expanded &&
        !treeQuery.isLoading &&
        rootFolders.map((folder) => (
          <FolderRow
            key={folder.id}
            folder={folder}
            childrenByParent={childrenByParent}
            selectedId={selectedId}
            onSelect={onSelect}
            disabledId={disabledId}
            depth={1}
          />
        ))}
    </>
  );
}

export function MoveFileDialog({
  open,
  onOpenChange,
  fileId,
  fileName,
  currentDriveId,
  currentParentFolderId,
}: MoveFileDialogProps) {
  const drivesQuery = useDrives();
  const moveFile = useMoveFile();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const handleConfirm = async () => {
    if (!selectedId) return;
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
        setSelectedId(null);
      },
      // 403s here are rare (we already strip shortcut destinations
      // from the picker) but keep the same friendly translation as
      // the drag-drop path so a misconfigured drive ACL doesn't show
      // the raw Drive error to the user.
      (err) =>
        err instanceof ApiError && err.status === 403
          ? "Drive denied this move — you may not have edit permission on the destination."
          : toErrorMessage(err, "Move failed"),
    );
  };

  // Disable the file's current parent so users can't no-op-move.
  const disabledId = currentParentFolderId ?? currentDriveId ?? "";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Block dismissal while the move is in flight so the user
        // can't escape-dismiss the spinner mid-request.
        if (moveFile.isPending) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Move &ldquo;{fileName}&rdquo;</DialogTitle>
          <DialogDescription>
            Pick a destination folder or drive. Cross-drive moves may be blocked by Drive admin
            settings — copy the file instead if that fails.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="h-72 rounded border">
          <div className="space-y-0.5 p-1">
            {(drivesQuery.data ?? []).map((drive) => (
              <DriveRow
                key={drive.id}
                driveId={drive.id}
                driveName={drive.name}
                selectedId={selectedId}
                onSelect={setSelectedId}
                disabledId={disabledId}
              />
            ))}
          </div>
        </ScrollArea>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              onOpenChange(false);
            }}
            disabled={moveFile.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => {
              void handleConfirm();
            }}
            disabled={!selectedId || moveFile.isPending}
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
