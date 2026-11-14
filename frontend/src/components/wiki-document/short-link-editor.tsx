"use client";

import { Check, Copy, Loader2, Trash2 } from "lucide-react";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useDocumentAlias,
  useRetireDocumentAlias,
  useSaveDocumentAlias,
} from "@/hooks/use-document-alias";
import { ApiError } from "@/lib/api-error";
import { documentAliasError } from "@/lib/document-alias-error";
import {
  documentShortLink,
  SHORT_LINK_DISPLAY_PREFIX,
} from "@/lib/document-short-link";
import { isAbortError } from "@/lib/mutation-toast";

import { useCopyFeedback } from "./use-copy-feedback";

type AliasEditState =
  | { readonly kind: "idle" | "confirming-remove" | "removing" | "unmounted" }
  | { readonly kind: "saving"; readonly value: string }
  | { readonly kind: "failed"; readonly message: string };

export function ShortLinkEditor(props: ShortLinkEditorProps) {
  return <EditorForFile key={props.fileId} {...props} />;
}

function EditorForFile({ fileId, onOpenChange, open }: ShortLinkEditorProps) {
  const [state, setState] = useState<AliasEditState>({ kind: "idle" });
  const phase = useRef<AliasEditState>(state);
  const publish = (next: AliasEditState) => {
    phase.current = next;
    setState(next);
  };
  useEffect(() => {
    phase.current = { kind: "idle" };
    return () => {
      phase.current = { kind: "unmounted" };
    };
  }, []);
  const [draft, setDraft] = useState<null | string>(null);
  const saveError = state.kind === "failed" ? state.message : null;
  const aliasQuery = useDocumentAlias(fileId, open);
  const saveAlias = useSaveDocumentAlias(fileId);
  const retireAlias = useRetireDocumentAlias(fileId);
  const savedAlias = aliasQuery.data?.alias;
  const { copied, showCopied } = useCopyFeedback(
    `${fileId}:${savedAlias ?? ""}`,
  );

  const value = draft ?? aliasQuery.data?.alias ?? "";
  const validationError = documentAliasError(value);
  const showValidationError = draft !== null && validationError !== null;
  const isSaving = aliasBusy(state);

  const handleSave = () => {
    if (
      validationError !== null ||
      (phase.current.kind !== "idle" && phase.current.kind !== "failed")
    )
      return;
    const saving: Extract<AliasEditState, { kind: "saving" }> = {
      kind: "saving",
      value,
    };
    publish(saving);
    void (async () => {
      const result = await saveAlias.mutateAsync(saving.value);
      if (phase.current !== saving) return;
      setDraft(result.alias);
      publish({ kind: "idle" });
      toast.success(savedAlias ? "Short link renamed" : "Short link created");
    })().catch((error: unknown) => {
      if (phase.current !== saving) return;
      publish(
        isAbortError(error)
          ? { kind: "idle" }
          : {
              kind: "failed",
              message: aliasErrorMessage(error, "Couldn't save short link"),
            },
      );
    });
  };

  const handleCopy = () => {
    if (!savedAlias) return;
    void (async () => {
      await navigator.clipboard.writeText(
        documentShortLink(window.location.origin, savedAlias),
      );
      showCopied();
    })().catch(() => toast.error("Couldn't copy short link"));
  };

  const handleRemove = () => {
    if (phase.current.kind !== "confirming-remove") return;
    const removing: AliasEditState = { kind: "removing" };
    publish(removing);
    void (async () => {
      await retireAlias.mutateAsync();
      if (phase.current !== removing) return;
      publish({ kind: "idle" });
      setDraft("");
      toast.success("Short link removed");
    })().catch((error: unknown) => {
      if (phase.current !== removing) return;
      publish(
        isAbortError(error)
          ? { kind: "idle" }
          : {
              kind: "failed",
              message: aliasErrorMessage(error, "Couldn't remove short link"),
            },
      );
    });
  };
  const close = () => {
    if (phase.current.kind === "saving" || phase.current.kind === "removing")
      return;
    publish({ kind: "idle" });
    setDraft(null);
    onOpenChange(false);
  };

  return (
    <>
      <Dialog
        onOpenChange={(nextOpen) => {
          if (!nextOpen) close();
          else onOpenChange(true);
        }}
        open={open}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Short link</DialogTitle>
            <DialogDescription>
              Give this document a memorable Wiki address that is easy to say
              and share.
            </DialogDescription>
          </DialogHeader>

          <ShortLinkQueryState
            error={aliasQuery.isError}
            pending={aliasQuery.isPending}
          >
            <div className="space-y-3">
              <Label htmlFor="document-alias">Wiki address</Label>
              <div className="border-input focus-within:border-ring focus-within:ring-ring/50 flex items-center rounded-md border shadow-xs focus-within:ring-[3px]">
                <span className="bg-muted text-muted-foreground border-input h-9 shrink-0 rounded-l-md border-r px-3 font-mono text-xs leading-9 select-none">
                  {SHORT_LINK_DISPLAY_PREFIX}
                </span>
                <Input
                  aria-describedby="document-alias-help document-alias-error"
                  aria-invalid={showValidationError || saveError !== null}
                  autoCapitalize="none"
                  autoComplete="off"
                  className="border-0 font-mono shadow-none focus-visible:ring-0"
                  disabled={isSaving}
                  id="document-alias"
                  onChange={(event) => {
                    setDraft(event.target.value);
                    if (phase.current.kind === "failed")
                      publish({ kind: "idle" });
                  }}
                  placeholder="eng-onboarding"
                  spellCheck={false}
                  value={value}
                />
                {savedAlias ? (
                  <Button
                    aria-label={
                      copied ? "Short link copied" : "Copy short link"
                    }
                    className="mr-1 shrink-0"
                    onClick={handleCopy}
                    size="icon-xs"
                    type="button"
                    variant="ghost"
                  >
                    {copied ? (
                      <Check className="size-3.5" />
                    ) : (
                      <Copy className="size-3.5" />
                    )}
                  </Button>
                ) : null}
              </div>
              <p
                className="text-muted-foreground text-xs"
                id="document-alias-help"
              >
                Use two or three lowercase words, with 2–12 letters per word.
                Names are never recycled after removal.
              </p>
              {showValidationError || saveError !== null ? (
                <p
                  className="text-destructive text-sm"
                  id="document-alias-error"
                  role="alert"
                >
                  {saveError ?? validationError}
                </p>
              ) : null}
            </div>
          </ShortLinkQueryState>

          <DialogFooter className="sm:justify-between">
            <div>
              {savedAlias ? (
                <Button
                  disabled={isSaving}
                  onClick={() => {
                    if (
                      phase.current.kind === "idle" ||
                      phase.current.kind === "failed"
                    )
                      publish({ kind: "confirming-remove" });
                  }}
                  type="button"
                  variant="ghost"
                >
                  <Trash2 className="size-4" />
                  Remove
                </Button>
              ) : null}
            </div>
            <div className="flex justify-end gap-2">
              <Button
                disabled={isSaving}
                onClick={close}
                type="button"
                variant="outline"
              >
                Close
              </Button>
              <Button
                disabled={
                  isSaving ||
                  validationError !== null ||
                  value === savedAlias ||
                  aliasQuery.isPending ||
                  aliasQuery.isError
                }
                onClick={handleSave}
                type="button"
              >
                {state.kind === "saving" ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : null}
                {savedAlias ? "Save changes" : "Create link"}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        onOpenChange={(next) => {
          if (!next && phase.current.kind === "confirming-remove")
            publish({ kind: "idle" });
        }}
        open={state.kind === "confirming-remove" || state.kind === "removing"}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this short link?</AlertDialogTitle>
            <AlertDialogDescription>
              The address will stop working and can never be assigned to another
              document.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={state.kind === "removing"}>
              Keep link
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={state.kind === "removing"}
              onClick={(event) => {
                event.preventDefault();
                handleRemove();
              }}
            >
              {state.kind === "removing" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : null}
              Remove link
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

interface ShortLinkEditorProps {
  fileId: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}

function aliasBusy(state: AliasEditState): boolean {
  switch (state.kind) {
    case "saving":
    case "removing":
      return true;
    case "idle":
    case "confirming-remove":
    case "failed":
    case "unmounted":
      return false;
    default:
      return unreachableAlias(state);
  }
}

function unreachableAlias(state: never): never {
  throw new Error(`Unhandled short-link state: ${JSON.stringify(state)}`);
}

function aliasErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 409)
    return "That short link is already taken";
  if (error instanceof ApiError && error.message.trim()) return error.message;
  return fallback;
}

function ShortLinkQueryState({
  children,
  error,
  pending,
}: {
  children: React.ReactNode;
  error: boolean;
  pending: boolean;
}) {
  if (pending)
    return (
      <div
        className="text-muted-foreground flex h-24 items-center justify-center gap-2 text-sm"
        role="status"
      >
        <Loader2 aria-hidden className="size-4 animate-spin" />
        Loading short link…
      </div>
    );
  if (error)
    return (
      <div
        className="border-destructive/30 bg-destructive/5 text-destructive rounded-md border px-3 py-2 text-sm"
        role="alert"
      >
        Couldn&apos;t load this document&apos;s short link.
      </div>
    );
  return children;
}
