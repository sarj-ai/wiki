"use client";

import {
  Copy,
  ExternalLink,
  FolderInput,
  Loader2,
  MoreHorizontal,
  Pencil,
  Trash2,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCopyFile, useDeleteFile, useRenameFile } from "@/hooks/use-wiki-mutations";
import { isGoogleWorkspaceType } from "@/lib/file-icons";
import { folderUrlFor, openUrlFor } from "@/lib/file-urls";
import { runWithErrorToast } from "@/lib/mutation-toast";
import { MIME_FORM } from "@/types/wiki";

import { MoveFileDialog } from "./move-file-dialog";
import { NamePromptDialog } from "./name-prompt-dialog";

interface ItemMenuProps {
  currentName: string;
  driveId: string | undefined;
  fileId: string;
  isFolder: boolean;
  parentFolderId: string | undefined;
}

export function ItemMenu({
  currentName,
  driveId,
  fileId,
  isFolder,
  parentFolderId,
}: ItemMenuProps) {
  const router = useRouter();
  const pathname = usePathname();
  const renameFile = useRenameFile();
  const deleteFile = useDeleteFile();
  const copyFile = useCopyFile();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [movingFile, setMovingFile] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [duplicating, setDuplicating] = useState(false);

  const handleRenameSubmit = async (next: string) => {
    if (next === currentName) return;
    await runWithErrorToast(async () => {
      await renameFile.mutateAsync({ fileId, name: next });
      toast.success("Renamed");
    }, "Rename failed");
  };

  const handleDuplicate = async (name: string) => {
    await runWithErrorToast(async () => {
      const file = await copyFile.mutateAsync({ fileId, name });

      if (file.mimeType === MIME_FORM) {
        window.open(openUrlFor(file.mimeType, file.id), "_blank", "noopener,noreferrer");
      } else if (isGoogleWorkspaceType(file.mimeType)) {
        router.push(`/d/${encodeURIComponent(file.id)}`);
      }
      toast.success("Duplicated");
    }, "Duplicate failed");
  };

  const handleDelete = async () => {
    await runWithErrorToast(async () => {
      await deleteFile.mutateAsync({ driveId, fileId, parentFolderId });
      setConfirmingDelete(false);
      toast.success(isFolder ? "Folder moved to trash" : "File moved to trash");

      if (pathname === `/d/${encodeURIComponent(fileId)}`) {
        router.push("/");
      }
    }, "Delete failed");
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            aria-label={`Actions for ${currentName}`}
            className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-5 shrink-0 rounded-sm"
            onClick={(e) => {
              e.stopPropagation();
            }}
            size="icon-xs"
            variant="ghost"
          >
            <MoreHorizontal className="size-3" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-40">
          <DropdownMenuItem
            className="flex items-center gap-2 text-sm"
            onClick={() => {
              setRenaming(true);
            }}
          >
            <Pencil className="size-4" />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem
            className="flex items-center gap-2 text-sm"
            onClick={() => {
              setMovingFile(true);
            }}
          >
            <FolderInput className="size-4" />
            Move to…
          </DropdownMenuItem>
          {!isFolder && (
            <DropdownMenuItem
              className="flex items-center gap-2 text-sm"
              onClick={() => {
                setDuplicating(true);
              }}
            >
              <Copy className="size-4" />
              Duplicate
            </DropdownMenuItem>
          )}
          {isFolder ? (
            <DropdownMenuItem asChild className="flex items-center gap-2 text-sm">
              <a href={folderUrlFor(fileId)} rel="noopener noreferrer" target="_blank">
                <ExternalLink className="size-4" />
                Open in Google Drive
              </a>
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem
            className="text-destructive focus:text-destructive flex items-center gap-2 text-sm"
            onClick={() => {
              setConfirmingDelete(true);
            }}
          >
            <Trash2 className="size-4" />
            Move to trash
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog
        onOpenChange={(open) => {
          if (deleteFile.isPending) return;
          setConfirmingDelete(open);
        }}
        open={confirmingDelete}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {isFolder ? "Move folder to trash?" : "Move file to trash?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              &ldquo;{currentName}&rdquo; will be moved to your Drive trash. You can restore it from
              drive.google.com within 30 days.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteFile.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleteFile.isPending}
              onClick={(e) => {
                e.preventDefault();
                void handleDelete();
              }}
            >
              {deleteFile.isPending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Moving to trash…
                </>
              ) : (
                "Move to trash"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <MoveFileDialog
        currentDriveId={driveId}
        currentParentFolderId={parentFolderId}
        fileId={fileId}
        fileName={currentName}
        onOpenChange={setMovingFile}
        open={movingFile}
      />
      <NamePromptDialog
        confirmLabel="Rename"
        defaultValue={currentName}
        onConfirm={handleRenameSubmit}
        onOpenChange={setRenaming}
        open={renaming}
        title={isFolder ? "Rename folder" : "Rename file"}
      />
      <NamePromptDialog
        confirmLabel="Duplicate"
        defaultValue={`Copy of ${currentName}`}
        description="Creates a copy as a sibling of the original."
        onConfirm={handleDuplicate}
        onOpenChange={setDuplicating}
        open={duplicating}
        title="Duplicate file"
      />
    </>
  );
}
