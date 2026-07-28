"use client";

import { ChevronRight, Folder, HardDrive, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

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
import type { DriveNode } from "@/types/wiki";
import { MIME_SHORTCUT } from "@/types/wiki";

import { rowIndent } from "./layout-constants";

interface MoveFileDialogProps {
  currentDriveId: string | undefined;
  currentParentFolderId: string | undefined;
  fileId: string;
  fileName: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}

interface FolderRowProps {
  childrenByParent: Map<string, DriveNode[]>;
  depth: number;
  disabledId: string;
  folder: DriveNode;
  onSelect: (id: string) => void;
  selectedId: null | string;
}

interface DriveRowProps {
  disabledId: string;
  driveId: string;
  driveName: string;
  onSelect: (id: string) => void;
  selectedId: null | string;
}

function FolderRow({
  childrenByParent,
  depth,
  disabledId,
  folder,
  onSelect,
  selectedId,
}: FolderRowProps) {
  const [expanded, setExpanded] = useState(false);
  const subFolders = childrenByParent.get(folder.id) ?? [];
  const isDisabled = folder.id === disabledId;

  return (
    <>
      <div className="flex items-center gap-1" style={{ paddingLeft: `${rowIndent(depth)}px` }}>
        <Button
          aria-label={expanded ? "Collapse" : "Expand"}
          className="hover:bg-muted size-4 shrink-0 rounded-sm"
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
            "h-7 flex-1 justify-start gap-1 px-2 font-normal",
            selectedId === folder.id && "bg-accent",
            isDisabled && "opacity-40",
          )}
          disabled={isDisabled}
          onClick={() => {
            onSelect(folder.id);
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
              onSelect={onSelect}
              selectedId={selectedId}
            />
          ))
        : null}
    </>
  );
}

function DriveRow({ disabledId, driveId, driveName, onSelect, selectedId }: DriveRowProps) {
  const [expanded, setExpanded] = useState(false);

  const treeQuery = useDriveTree(expanded ? driveId : null);

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
      <div className="flex items-center gap-1">
        <Button
          aria-label={expanded ? "Collapse" : "Expand"}
          className="hover:bg-muted size-4 shrink-0 rounded-sm"
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
            "h-7 flex-1 justify-start gap-1 px-2",
            selectedId === driveId && !isDriveDisabled && "bg-accent",
            isDriveDisabled && "opacity-60",
          )}
          disabled={isDriveDisabled}
          onClick={() => {
            onSelect(driveId);
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
        <div className="text-muted-foreground flex items-center gap-2 px-4 py-1 text-xs">
          <Loader2 className="size-3 animate-spin" />
          Loading folders…
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
              onSelect={onSelect}
              selectedId={selectedId}
            />
          ))
        : null}
    </>
  );
}

export function MoveFileDialog({
  currentDriveId,
  currentParentFolderId,
  fileId,
  fileName,
  onOpenChange,
  open,
}: MoveFileDialogProps) {
  const drivesQuery = useDrives();
  const moveFile = useMoveFile();
  const [selectedId, setSelectedId] = useState<null | string>(null);

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

      (err) =>
        err instanceof ApiError && err.status === 403
          ? "Drive denied this move — you may not have edit permission on the destination."
          : toErrorMessage(err, "Move failed"),
    );
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
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Move &ldquo;{fileName}&rdquo;</DialogTitle>
          <DialogDescription>
            Pick a destination folder or drive. Cross-drive moves may be blocked by Drive admin
            settings — copy the file instead if that fails.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="h-72 rounded-sm border">
          <div className="space-y-0.5 p-1">
            {(drivesQuery.data ?? []).map((drive) => (
              <DriveRow
                disabledId={disabledId}
                driveId={drive.id}
                driveName={drive.name}
                key={drive.id}
                onSelect={setSelectedId}
                selectedId={selectedId}
              />
            ))}
          </div>
        </ScrollArea>
        <DialogFooter>
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
              void handleConfirm();
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
