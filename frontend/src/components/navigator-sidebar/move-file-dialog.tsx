"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Folder, HardDrive, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import type { DriveNode, DriveTree } from "@/types/navigator";

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
import { useMoveFile } from "@/hooks/use-navigator-mutations";
import { navigatorKeys, useDrives } from "@/hooks/use-navigator-tree";
import { cn } from "@/lib/utils";

interface MoveFileDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  fileId: string;
  fileName: string;
  currentDriveId: string | null;
  currentParentFolderId: string | null;
}

function buildChildrenIndex(folders: DriveNode[]): Map<string, DriveNode[]> {
  const map = new Map<string, DriveNode[]>();
  for (const folder of folders) {
    const key = folder.parentId ?? "__root__";
    const bucket = map.get(key);
    if (bucket) bucket.push(folder);
    else map.set(key, [folder]);
  }
  return map;
}

interface FolderRowProps {
  folder: DriveNode;
  childrenByParent: Map<string, DriveNode[]>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  disabledId: string;
  depth: number;
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
      <div
        className={cn(
          "hover:bg-accent flex cursor-pointer items-center gap-1 rounded px-2 py-1 text-sm",
          selectedId === folder.id && "bg-accent",
          isDisabled && "pointer-events-none opacity-40",
        )}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        onClick={() => { if (!isDisabled) onSelect(folder.id); }}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          className="hover:bg-muted flex size-4 shrink-0 items-center justify-center rounded"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {subFolders.length > 0 && (
            <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
          )}
        </button>
        <Folder className="size-4 shrink-0" />
        <span className="truncate">{folder.name}</span>
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

interface DriveRowProps {
  driveId: string;
  driveName: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  disabledId: string;
  // True when this drive is not the file's source drive. Drive's API
  // rejects cross-shared-drive parent changes, so the row is rendered
  // for context but locked from selection/expansion.
  crossDrive: boolean;
}

// Reads the drive-tree cache entry from React Query (not a new fetch —
// the sidebar already populated it). If the user opens the move dialog
// before ever expanding that drive in the sidebar, the tree may not be
// cached yet and the list stays empty; that's acceptable UX since they
// can still pick any drive they HAVE expanded.
function DriveRow({
  driveId,
  driveName,
  selectedId,
  onSelect,
  disabledId,
  crossDrive,
}: DriveRowProps) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const cachedTree = qc.getQueryData<DriveTree>(navigatorKeys.driveTree(driveId));
  const childrenByParent = useMemo(
    () => buildChildrenIndex(cachedTree?.folders ?? []),
    [cachedTree],
  );
  const rootFolders = childrenByParent.get("__root__") ?? [];
  const isDriveDisabled = driveId === disabledId || crossDrive;

  return (
    <>
      <div
        className={cn(
          "flex items-center gap-1 rounded px-2 py-1 text-sm font-medium",
          !isDriveDisabled && "hover:bg-accent cursor-pointer",
          selectedId === driveId && !isDriveDisabled && "bg-accent",
          isDriveDisabled && "cursor-not-allowed opacity-40",
        )}
        title={crossDrive ? "Drive doesn't allow moves between shared drives — copy instead" : undefined}
        onClick={() => { if (!isDriveDisabled) onSelect(driveId); }}
      >
        <button
          type="button"
          disabled={crossDrive}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          className="hover:bg-muted flex size-4 shrink-0 items-center justify-center rounded disabled:cursor-not-allowed disabled:hover:bg-transparent"
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          <ChevronRight className={cn("size-3 transition-transform", expanded && "rotate-90")} />
        </button>
        <HardDrive className="size-4 shrink-0" />
        <span className="truncate">{driveName}</span>
        {crossDrive && (
          <span className="text-muted-foreground ml-auto shrink-0 text-xs">cross-drive</span>
        )}
      </div>
      {expanded && !crossDrive &&
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
    try {
      await moveFile.mutateAsync({
        fileId,
        newParentFolderId: selectedId,
        sourceDriveId: currentDriveId,
      });
      toast.success(`Moved "${fileName}"`);
      onOpenChange(false);
      setSelectedId(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Move failed";
      toast.error(message);
    }
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
            Pick a destination folder within this drive. Google Drive doesn&apos;t support moving
            files across shared drives — copy the file into the other drive instead.
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
                // Drive's API rejects parent changes that cross shared
                // drives — render the row for context but lock it.
                crossDrive={drive.id !== currentDriveId}
              />
            ))}
          </div>
        </ScrollArea>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => { onOpenChange(false); }}
            disabled={moveFile.isPending}
          >
            Cancel
          </Button>
          <Button onClick={() => { void handleConfirm(); }} disabled={!selectedId || moveFile.isPending}>
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
