"use client";

import {
  ArrowRight,
  Folder,
  Loader2,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
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
import { Button } from "@/components/ui/button";
import { useMoveFile } from "@/hooks/use-wiki-mutations";
import { isAbortError, toErrorMessage } from "@/lib/mutation-toast";
import {
  proposeFileReorganization,
  type ReorganizationDestination,
  type ReorganizationProposal,
} from "@/lib/reorganize-client";

type ActionableProposal = ReorganizationProposal & {
  suggestedDestination: ReorganizationDestination;
};

type ReorganizationState =
  | { kind: "idle" }
  | { kind: "checking"; owner: object }
  | { kind: "proposed"; proposal: ActionableProposal };

export function ReorganizeDocument({ ...props }: ReorganizeDocumentProps) {
  return <ReorganizeDocumentForFile key={props.fileId} {...props} />;
}

function ReorganizeDocumentForFile({
  driveId,
  fileId,
  fileName,
  parentFolderId,
}: ReorganizeDocumentProps) {
  const [state, setState] = useState<ReorganizationState>({ kind: "idle" });
  const phase = useRef<ReorganizationState>(state);
  const mounted = useRef(true);
  const checking = state.kind === "checking";
  const proposal = state.kind === "proposed" ? state.proposal : null;
  const publish = (next: ReorganizationState) => {
    phase.current = next;
    setState(next);
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      phase.current = { kind: "idle" };
    };
  }, []);

  const propose = async () => {
    if (phase.current.kind !== "idle") return;
    const owner = {};
    publish({ kind: "checking", owner });
    const current = () =>
      mounted.current &&
      phase.current.kind === "checking" &&
      phase.current.owner === owner;
    try {
      const result = await proposeFileReorganization(fileId);
      if (!current()) return;
      if (result.suggestedDestination === undefined) {
        toast.success("This document is already in the best location");
        return;
      }
      publish({
        kind: "proposed",
        proposal: {
          ...result,
          suggestedDestination: result.suggestedDestination,
        },
      });
    } catch (error: unknown) {
      if (current() && !isAbortError(error)) {
        toast.error(
          toErrorMessage(error, "Couldn’t check this document’s location"),
        );
      }
    } finally {
      if (current()) publish({ kind: "idle" });
    }
  };

  return (
    <>
      <Button
        aria-label={
          checking ? "Checking document location" : "Re-organize document"
        }
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
        <span className="hidden @min-[40rem]/toolbar:inline">
          {checking ? "Checking…" : "Re-organize"}
        </span>
      </Button>
      {proposal === null ? null : (
        <ReorganizationConfirmation
          driveId={driveId}
          fileId={fileId}
          fileName={fileName}
          onClose={() => publish({ kind: "idle" })}
          parentFolderId={parentFolderId}
          proposal={proposal}
        />
      )}
    </>
  );
}

function ReorganizationConfirmation({
  driveId,
  fileId,
  fileName,
  onClose,
  parentFolderId,
  proposal,
}: ReorganizationConfirmationProps) {
  const moveFile = useMoveFile();
  const mounted = useRef(true);
  const moveLatch = useRef<null | object>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      moveLatch.current = null;
    };
  }, []);

  const move = async () => {
    if (moveLatch.current) return;
    const destination = proposal.suggestedDestination;
    const latch = {};
    moveLatch.current = latch;
    try {
      await moveFile.mutateAsync({
        fileId,
        newParentFolderId: destination.folderId,
        sourceDriveId: driveId,
        sourceParentFolderId: parentFolderId,
      });
      if (!mounted.current) return;
      toast.success(`Moved “${fileName}” to ${destinationLabel(destination)}`);
      onClose();
    } catch (error: unknown) {
      if (mounted.current && !isAbortError(error)) {
        toast.error(toErrorMessage(error, "Couldn’t move this document"));
      }
    } finally {
      releaseLatch(moveLatch, latch);
    }
  };

  return (
    <AlertDialog
      onOpenChange={(open) => {
        if (!open && !moveFile.isPending) onClose();
      }}
      open
    >
      <AlertDialogContent className="max-h-[calc(100dvh-1rem)] overflow-y-auto">
        <AlertDialogHeader>
          <div className="bg-destructive/10 text-destructive flex size-9 items-center justify-center rounded-full">
            <TriangleAlert className="size-4" />
          </div>
          <AlertDialogTitle>Move this document?</AlertDialogTitle>
          <AlertDialogDescription>
            Re-organize found a better location within{" "}
            {proposal.currentDestination.driveName}. Moving it changes where
            teammates find it in Google Drive.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="bg-muted/50 grid grid-cols-1 gap-2 rounded-lg border p-3 sm:grid-cols-[1fr_auto_1fr] sm:items-center sm:gap-3">
          <Location destination={proposal.currentDestination} label="Current" />
          <ArrowRight className="text-muted-foreground mx-auto size-4 rotate-90 sm:rotate-0" />
          <Location
            destination={proposal.suggestedDestination}
            label="Recommended"
          />
        </div>
        {proposal.reason === undefined ? null : (
          <p className="text-muted-foreground text-sm">{proposal.reason}</p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={moveFile.isPending}>
            Keep it here
          </AlertDialogCancel>
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
  );
}

interface ReorganizeDocumentProps {
  driveId: string | undefined;
  fileId: string;
  fileName: string;
  parentFolderId: string | undefined;
}

interface ReorganizationConfirmationProps extends ReorganizeDocumentProps {
  onClose: () => void;
  proposal: ActionableProposal;
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
        <span className="min-w-0 flex-1 wrap-anywhere">
          {destinationLabel(destination)}
        </span>
      </div>
    </div>
  );
}

function destinationLabel(destination: ReorganizationDestination): string {
  return destination.folderPath.join(" / ") || destination.driveName;
}

function releaseLatch(
  latchRef: { current: null | object },
  marker: object,
): void {
  if (latchRef.current === marker) latchRef.current = null;
}
