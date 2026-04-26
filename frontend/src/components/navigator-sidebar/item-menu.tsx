"use client";

import { Copy, FolderInput, Loader2, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCopyFile, useDeleteFile, useRenameFile } from "@/hooks/use-navigator-mutations";
import { isGoogleWorkspaceType } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { MIME_FORM } from "@/types/navigator";

import { MoveFileDialog } from "./move-file-dialog";
import { NamePromptDialog } from "./name-prompt-dialog";

interface ItemMenuProps {
  fileId: string;
  driveId: string | null;
  parentFolderId: string | null;
  currentName: string;
  isFolder: boolean;
}

export function ItemMenu({
  fileId,
  driveId,
  parentFolderId,
  currentName,
  isFolder,
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
    try {
      await renameFile.mutateAsync({ fileId, name: next });
      toast.success("Renamed");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Rename failed";
      toast.error(message);
    }
  };

  const handleDuplicate = async (name: string) => {
    try {
      const file = await copyFile.mutateAsync({ fileId, name });
      // Forms can't be iframed (see file-row.tsx) so the duplicate
      // opens in a new tab. Other Workspace types open inline.
      if (file.mimeType === MIME_FORM) {
        window.open(openUrlFor(file.mimeType, file.id), "_blank", "noopener,noreferrer");
      } else if (isGoogleWorkspaceType(file.mimeType)) {
        router.push(`/d/${encodeURIComponent(file.id)}`);
      }
      toast.success("Duplicated");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Duplicate failed";
      toast.error(message);
    }
  };

  const handleDelete = async () => {
    // Keep the dialog open while the delete is in flight so the
    // confirm button can show a spinner and block double-clicks —
    // closing eagerly and relying on the toast leaves the user
    // without any feedback during the round-trip.
    try {
      await deleteFile.mutateAsync({ fileId, driveId, parentFolderId });
      setConfirmingDelete(false);
      toast.success(isFolder ? "Folder moved to trash" : "File moved to trash");
      // If the user is currently viewing the deleted file's iframe,
      // route them back to the landing page — otherwise the viewer
      // would hang on a file that now 404s (or worse, keep showing
      // the iframe of a trashed doc).
      if (pathname === `/d/${encodeURIComponent(fileId)}`) {
        router.push("/");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Delete failed";
      toast.error(message);
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); }}
            className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-5 shrink-0 items-center justify-center rounded outline-none"
            aria-label={`Actions for ${currentName}`}
          >
            <MoreHorizontal className="size-3" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-40">
          <DropdownMenuItem
            onClick={() => { setRenaming(true); }}
            className="flex items-center gap-2 text-sm"
          >
            <Pencil className="size-4" />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => { setMovingFile(true); }}
            className="flex items-center gap-2 text-sm"
          >
            <FolderInput className="size-4" />
            Move to…
          </DropdownMenuItem>
          {!isFolder && (
            <DropdownMenuItem
              onClick={() => { setDuplicating(true); }}
              className="flex items-center gap-2 text-sm"
            >
              <Copy className="size-4" />
              Duplicate
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            onClick={() => { setConfirmingDelete(true); }}
            className="text-destructive focus:text-destructive flex items-center gap-2 text-sm"
          >
            <Trash2 className="size-4" />
            Move to trash
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog
        open={confirmingDelete}
        onOpenChange={(open) => {
          // Block closing while the delete is in flight so the user
          // can't escape-dismiss the spinner mid-request.
          if (deleteFile.isPending) return;
          setConfirmingDelete(open);
        }}
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
              onClick={(e) => {
                // radix closes the alert on click by default. Override
                // that so the dialog stays open until the delete
                // settles — the `onOpenChange` guard above enforces it.
                e.preventDefault();
                void handleDelete();
              }}
              disabled={deleteFile.isPending}
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
        open={movingFile}
        onOpenChange={setMovingFile}
        fileId={fileId}
        fileName={currentName}
        currentDriveId={driveId}
        currentParentFolderId={parentFolderId}
      />
      <NamePromptDialog
        open={renaming}
        onOpenChange={setRenaming}
        title={isFolder ? "Rename folder" : "Rename file"}
        defaultValue={currentName}
        confirmLabel="Rename"
        onConfirm={handleRenameSubmit}
      />
      <NamePromptDialog
        open={duplicating}
        onOpenChange={setDuplicating}
        title="Duplicate file"
        description="Creates a copy as a sibling of the original."
        defaultValue={`Copy of ${currentName}`}
        confirmLabel="Duplicate"
        onConfirm={handleDuplicate}
      />
    </>
  );
}
