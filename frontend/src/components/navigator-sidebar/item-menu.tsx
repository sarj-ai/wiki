"use client";

import { useState } from "react";
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
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
import { useDeleteFile, useRenameFile } from "@/hooks/use-navigator-mutations";

interface ItemMenuProps {
  fileId: string;
  driveId: string | null;
  currentName: string;
  isFolder: boolean;
}

export function ItemMenu({ fileId, driveId, currentName, isFolder }: ItemMenuProps) {
  const renameFile = useRenameFile();
  const deleteFile = useDeleteFile();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const handleRename = async () => {
    const next = window.prompt("Rename to", currentName);
    if (!next || next.trim() === "" || next === currentName) return;
    try {
      await renameFile.mutateAsync({ fileId, name: next.trim() });
      toast.success("Renamed");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Rename failed";
      toast.error(message);
    }
  };

  const handleDelete = async () => {
    setConfirmingDelete(false);
    try {
      await deleteFile.mutateAsync({ fileId, driveId });
      toast.success(isFolder ? "Folder moved to trash" : "File moved to trash");
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
            onClick={(e) => e.stopPropagation()}
            className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-5 shrink-0 items-center justify-center rounded outline-none"
            aria-label={`Actions for ${currentName}`}
          >
            <MoreHorizontal className="size-3" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-40">
          <DropdownMenuItem onClick={handleRename} className="flex items-center gap-2 text-sm">
            <Pencil className="size-4" />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => setConfirmingDelete(true)}
            className="text-destructive focus:text-destructive flex items-center gap-2 text-sm"
          >
            <Trash2 className="size-4" />
            Move to trash
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
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
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>Move to trash</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
