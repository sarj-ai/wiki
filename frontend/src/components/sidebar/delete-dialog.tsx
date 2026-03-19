"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { Page } from "@/types";

interface DeletePageDialogProps {
  deletingPageId: string | null;
  deletingPage: Page | null | undefined;
  deleteChildCount: number;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeletePageDialog({
  deletingPageId,
  deletingPage,
  deleteChildCount,
  onClose,
  onConfirm,
}: DeletePageDialogProps) {
  return (
    <Dialog open={!!deletingPageId} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete page?</DialogTitle>
          <DialogDescription>
            {deleteChildCount > 0
              ? `This will permanently delete "${deletingPage?.title}" and ${deleteChildCount} child page${deleteChildCount > 1 ? "s" : ""}. This action cannot be undone.`
              : `This will permanently delete "${deletingPage?.title}". This action cannot be undone.`}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            Delete{deleteChildCount > 0 ? ` (${deleteChildCount + 1} pages)` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
