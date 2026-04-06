"use client";

import { useState } from "react";
import type { Workspace } from "@/types";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";

interface MoveToWorkspaceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaces: Workspace[];
  currentWorkspaceId: string | null;
  onConfirm: (workspaceId: string) => Promise<void>;
}

export function MoveToWorkspaceDialog({
  open,
  onOpenChange,
  workspaces,
  currentWorkspaceId,
  onConfirm,
}: MoveToWorkspaceDialogProps) {
  const [selectedId, setSelectedId] = useState("");
  const [isMoving, setIsMoving] = useState(false);

  const availableWorkspaces = workspaces.filter((w) => w.id !== currentWorkspaceId);

  async function handleConfirm() {
    if (!selectedId) return;
    setIsMoving(true);
    try {
      await onConfirm(selectedId);
      onOpenChange(false);
    } finally {
      setIsMoving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[400px]">
        <DialogHeader>
          <DialogTitle>Move to workspace</DialogTitle>
          <DialogDescription>
            The page and all its sub-pages will be moved to the selected workspace.
          </DialogDescription>
        </DialogHeader>
        <Select value={selectedId} onValueChange={setSelectedId}>
          <SelectTrigger>
            <SelectValue placeholder="Select a workspace..." />
          </SelectTrigger>
          <SelectContent>
            {availableWorkspaces.map((ws) => (
              <SelectItem key={ws.id} value={ws.id}>
                {ws.icon ? `${ws.icon} ` : ""}{ws.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleConfirm} disabled={!selectedId || isMoving}>
            {isMoving ? <Loader2 className="mr-2 size-4 animate-spin" /> : null}
            Move
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
