"use client";

import { ArrowRight, Folder, Loader2, Sparkles, TriangleAlert } from "lucide-react";
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
import { useMoveFile } from "@/hooks/use-wiki-mutations";
import { toErrorMessage } from "@/lib/mutation-toast";
import {
  proposeFileReorganization,
  type ReorganizationDestination,
  type ReorganizationProposal,
} from "@/lib/reorganize-client";

export function ReorganizeDocument({
  driveId,
  fileId,
  fileName,
  parentFolderId,
}: ReorganizeDocumentProps) {
  const moveFile = useMoveFile();
  const [checking, setChecking] = useState(false);
  const [proposal, setProposal] = useState<null | ReorganizationProposal>(null);

  const propose = async () => {
    setChecking(true);
    try {
      const result = await proposeFileReorganization(fileId);
      if (result.suggestedDestination === undefined) {
        toast.success("This document is already in the best location");
        return;
      }
      setProposal(result);
    } catch (error: unknown) {
      toast.error(toErrorMessage(error, "Couldn’t check this document’s location"));
    } finally {
      setChecking(false);
    }
  };

  const move = async () => {
    const destination = proposal?.suggestedDestination;
    if (destination === undefined) return;
    try {
      await moveFile.mutateAsync({
        fileId,
        newParentFolderId: destination.folderId,
        sourceDriveId: driveId,
        sourceParentFolderId: parentFolderId,
      });
      toast.success(`Moved “${fileName}” to ${destinationLabel(destination)}`);
      setProposal(null);
    } catch (error: unknown) {
      toast.error(toErrorMessage(error, "Couldn’t move this document"));
    }
  };

  return (
    <>
      <Button
        disabled={checking}
        onClick={() => {
          void propose();
        }}
        size="xs"
        type="button"
        variant="outline"
      >
        {checking ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Sparkles className="size-3.5" />
        )}
        {checking ? "Checking…" : "Re-organize"}
      </Button>
      <AlertDialog
        onOpenChange={(open) => {
          if (!open && !moveFile.isPending) setProposal(null);
        }}
        open={proposal !== null}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <div className="bg-destructive/10 text-destructive flex size-9 items-center justify-center rounded-full">
              <TriangleAlert className="size-4" />
            </div>
            <AlertDialogTitle>Move this document?</AlertDialogTitle>
            <AlertDialogDescription>
              Re-organize found a better location within {proposal?.currentDestination.driveName}.
              Moving it changes where teammates find it in Google Drive.
            </AlertDialogDescription>
          </AlertDialogHeader>
          {proposal?.suggestedDestination === undefined ? null : (
            <div className="bg-muted/50 grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-lg border p-3">
              <Location destination={proposal.currentDestination} label="Current" />
              <ArrowRight className="text-muted-foreground size-4" />
              <Location destination={proposal.suggestedDestination} label="Recommended" />
            </div>
          )}
          {proposal?.reason === undefined ? null : (
            <p className="text-muted-foreground text-sm">{proposal.reason}</p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={moveFile.isPending}>Keep it here</AlertDialogCancel>
            <AlertDialogAction
              disabled={moveFile.isPending}
              onClick={(event) => {
                event.preventDefault();
                void move();
              }}
            >
              {moveFile.isPending ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Moving…
                </>
              ) : (
                "Move document"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

interface ReorganizeDocumentProps {
  driveId: string | undefined;
  fileId: string;
  fileName: string;
  parentFolderId: string | undefined;
}

function Location({
  destination,
  label,
}: {
  destination: ReorganizationDestination;
  label: string;
}) {
  return (
    <div className="min-w-0">
      <span className="text-muted-foreground text-[11px] font-semibold tracking-wide uppercase">
        {label}
      </span>
      <div className="mt-1 flex items-start gap-1.5 text-sm font-medium">
        <Folder className="mt-0.5 size-3.5 shrink-0" />
        <span className="break-words">{destinationLabel(destination)}</span>
      </div>
    </div>
  );
}

function destinationLabel(destination: ReorganizationDestination): string {
  return destination.folderPath.join(" / ") || destination.driveName;
}
