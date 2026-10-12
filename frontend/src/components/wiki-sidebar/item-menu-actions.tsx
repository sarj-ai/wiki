"use client";

import { Copy, ExternalLink, FolderInput, Loader2, Pencil, Trash2 } from "lucide-react";
import dynamic from "next/dynamic";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
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
import { DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";
import { useCopyFile, useDeleteFile, useRenameFile } from "@/hooks/use-wiki-mutations";
import { isGoogleWorkspaceType } from "@/lib/file-icons";
import { folderUrlFor, openUrlFor } from "@/lib/file-urls";
import { runWithErrorToast } from "@/lib/mutation-toast";
import { MIME_FORM } from "@/types/wiki";

import type { ItemMenuProps } from "./item-menu";
import { NamePromptDialog } from "./name-prompt-dialog";

const MoveFileDialog = dynamic(
  async () => {
    const loadedModule = await import("./move-file-dialog");
    return loadedModule.MoveFileDialog;
  },
  { ssr: false },
);

export function ItemMenuActions({
  currentName,
  driveId,
  fileId,
  isFolder,
  isMoveScopeActive,
  menuOpen,
  moveInOutlet = false,
  onIdle,
  onMove,
  parentFolderId,
}: ItemMenuProps & {
  isMoveScopeActive?: () => boolean;
  menuOpen: boolean;
  moveInOutlet?: boolean;
  onIdle: () => void;
  onMove?: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const renameFile = useRenameFile();
  const deleteFile = useDeleteFile();
  const copyFile = useCopyFile();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const moveDialog = useDeferredDialog();
  const moveContent = useRef<HTMLElement | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [duplicating, setDuplicating] = useState(false);

  useEffect(() => {
    if (
      menuOpen ||
      renaming ||
      duplicating ||
      confirmingDelete ||
      moveDialog.open ||
      moveInOutlet ||
      renameFile.isPending ||
      copyFile.isPending ||
      deleteFile.isPending
    )
      return;
    // Keep the native dropdown's dismissal/focus work mounted, then retire
    // used action observers only after all descendant tasks have settled.
    const timer = window.setTimeout(onIdle, 200);
    return () => {
      window.clearTimeout(timer);
    };
  }, [
    confirmingDelete,
    copyFile.isPending,
    deleteFile.isPending,
    duplicating,
    menuOpen,
    moveDialog.open,
    moveInOutlet,
    onIdle,
    renameFile.isPending,
    renaming,
  ]);

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
      <DropdownMenuContent
        align="start"
        className="w-40"
        onCloseAutoFocus={(event) => {
          if (
            renaming ||
            duplicating ||
            confirmingDelete ||
            moveContent.current?.isConnected ||
            isMoveScopeActive?.()
          )
            event.preventDefault();
        }}
      >
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
            if (onMove) onMove();
            else moveDialog.onOpenChange(true);
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
                void handleDelete().catch((error: unknown) => {
                  toast.error(error instanceof Error ? error.message : "Delete failed");
                });
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
      {moveDialog.mounted ? (
        <MoveFileDialog
          currentDriveId={driveId}
          currentParentFolderId={parentFolderId}
          fileId={fileId}
          fileName={currentName}
          onCloseAutoFocus={(event) => {
            if (event.target === moveContent.current) moveContent.current = null;
          }}
          onOpenAutoFocus={(event) => {
            moveContent.current = event.target instanceof HTMLElement ? event.target : null;
          }}
          onOpenChange={moveDialog.onOpenChange}
          open={moveDialog.open}
        />
      ) : null}
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
