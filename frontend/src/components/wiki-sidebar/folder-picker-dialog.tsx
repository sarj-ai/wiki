"use client";

import { ChevronRight, Folder, HardDrive, Loader2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useDrives, useDriveTree } from "@/hooks/use-wiki-tree";
import type { SmartDestination } from "@/lib/smart-create-types";
import { cn } from "@/lib/utils";
import { EMPTY_NODES, indexDestinationFolders } from "@/lib/wiki-indexes";
import type { DriveNode } from "@/types/wiki";

import { DriveListFeedback } from "./drive-list-feedback";

export function FolderPickerDialog({
  allowedDriveId,
  onOpenChange,
  onSelect,
  open,
}: FolderPickerDialogProps) {
  const drives = useDrives();
  const visibleDrives = (drives.data ?? []).filter(
    (drive) => allowedDriveId === undefined || drive.id === allowedDriveId,
  );
  const hasNoDestinations = drives.isSuccess && visibleDrives.length === 0;
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Choose destination</DialogTitle>
          <DialogDescription>Select a workspace or folder for the new file.</DialogDescription>
        </DialogHeader>
        <ScrollArea className="h-[min(20rem,50dvh)] rounded-md border">
          <div className="space-y-0.5 p-1.5">
            <DriveListFeedback
              empty={hasNoDestinations}
              emptyMessage="No available destinations."
              query={drives}
            />
            {visibleDrives.map((drive) => (
              <DriveFolderRow
                driveId={drive.id}
                driveName={drive.name}
                key={drive.id}
                onSelect={(destination) => {
                  onSelect(destination);
                  onOpenChange(false);
                }}
              />
            ))}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

interface FolderPickerDialogProps {
  allowedDriveId?: string;
  onOpenChange: (open: boolean) => void;
  onSelect: (destination: SmartDestination) => void;
  open: boolean;
}

interface DriveFolderRowProps {
  driveId: string;
  driveName: string;
  onSelect: (destination: SmartDestination) => void;
}

function DriveFolderRow({ driveId, driveName, onSelect }: DriveFolderRowProps) {
  const [expanded, setExpanded] = useState(false);
  const tree = useDriveTree(expanded ? driveId : null);
  const children = indexDestinationFolders(tree.data?.folders ?? EMPTY_NODES);
  const roots = children.get("__root__") ?? EMPTY_NODES;

  return (
    <>
      <div className="flex items-center gap-1">
        <Button
          aria-label={expanded ? `Collapse ${driveName}` : `Expand ${driveName}`}
          className="size-6 shrink-0"
          onClick={() => {
            setExpanded((current) => !current);
          }}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          <ChevronRight className={cn("size-3.5 transition-transform", expanded && "rotate-90")} />
        </Button>
        <Button
          className="h-8 min-w-0 flex-1 justify-start gap-2 px-2"
          onClick={() => {
            onSelect({ driveId, driveName, folderId: driveId, folderPath: [] });
          }}
          size="sm"
          type="button"
          variant="ghost"
        >
          <HardDrive className="size-4" />
          <span className="truncate">{driveName}</span>
        </Button>
      </div>
      {expanded && tree.isPending ? (
        <div className="text-muted-foreground flex items-center gap-2 py-1 pl-8 text-xs">
          <Loader2 className="size-3 animate-spin" /> Loading…
        </div>
      ) : null}
      {expanded && tree.isError ? (
        <div className="space-y-2 py-2 pl-8 text-xs" role="alert">
          <p>Folders could not be loaded.</p>
          <Button
            disabled={tree.isFetching}
            onClick={() => {
              void tree.refetch().catch(() => console.warn("Folder retry could not finish"));
            }}
            size="sm"
            type="button"
            variant="outline"
          >
            Retry folders
          </Button>
        </div>
      ) : null}
      {expanded
        ? roots.map((folder) => (
            <FolderRow
              ancestors={[]}
              childrenByParent={children}
              driveId={driveId}
              driveName={driveName}
              folder={folder}
              key={folder.id}
              onSelect={onSelect}
            />
          ))
        : null}
    </>
  );
}

interface FolderRowProps extends DriveFolderRowProps {
  ancestors: string[];
  childrenByParent: Map<string, DriveNode[]>;
  folder: DriveNode;
}

function FolderRow({
  ancestors,
  childrenByParent,
  driveId,
  driveName,
  folder,
  onSelect,
}: FolderRowProps) {
  const [expanded, setExpanded] = useState(false);
  const descendants = childrenByParent.get(folder.id) ?? EMPTY_NODES;
  const path = expanded ? [...ancestors, folder.name] : ancestors;
  return (
    <>
      <div className="flex items-center gap-1">
        <Button
          aria-label={expanded ? `Collapse ${folder.name}` : `Expand ${folder.name}`}
          className="size-6 shrink-0"
          disabled={descendants.length === 0}
          onClick={() => {
            setExpanded((current) => !current);
          }}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          {descendants.length > 0 ? (
            <ChevronRight
              className={cn("size-3.5 transition-transform", expanded && "rotate-90")}
            />
          ) : null}
        </Button>
        <Button
          className="h-8 min-w-0 flex-1 justify-start gap-2 px-2 font-normal"
          onClick={() => {
            onSelect({
              driveId,
              driveName,
              folderId: folder.id,
              folderPath: expanded ? path : [...ancestors, folder.name],
            });
          }}
          size="sm"
          type="button"
          variant="ghost"
        >
          <Folder className="size-4" />
          <span className="truncate">{folder.name}</span>
        </Button>
      </div>
      {expanded ? (
        <div className="pl-4">
          {descendants.map((child) => (
            <FolderRow
              ancestors={path}
              childrenByParent={childrenByParent}
              driveId={driveId}
              driveName={driveName}
              folder={child}
              key={child.id}
              onSelect={onSelect}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}
