"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

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

interface NamePromptDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  // Pre-populated value — used as both the initial input content and
  // the default that surfaces every time the dialog reopens.
  defaultValue?: string;
  confirmLabel?: string;
  onConfirm: (value: string) => void | Promise<void>;
}

export function NamePromptDialog({
  open,
  onOpenChange,
  title,
  description,
  defaultValue = "",
  confirmLabel = "Create",
  onConfirm,
}: NamePromptDialogProps) {
  const [value, setValue] = useState(defaultValue);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Reset the field whenever the dialog opens so consecutive uses don't
  // carry over a stale value from the previous run. The "reset on transition"
  // pattern is the whole point of this effect — setState on open is the
  // feature, not a bug.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (open) setValue(defaultValue);
  }, [open, defaultValue]);

  const handleSubmit = async () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setSubmitting(true);
    try {
      await onConfirm(trimmed);
      onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-sm"
        // Take over the open-auto-focus cycle: prevent radix's
        // default "focus the first focusable child" behavior (which
        // competes with our Input's autoFocus and strips any text
        // selection) and do the focus + select ourselves on the
        // next animation frame, so the dialog's open transition
        // has settled before we call .select().
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          requestAnimationFrame(() => {
            const el = inputRef.current;
            if (!el) return;
            el.focus();
            el.select();
          });
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void handleSubmit();
          }}
          className="space-y-3"
        >
          <div className="space-y-1.5">
            <Label htmlFor="name-prompt-input">Name</Label>
            <Input
              ref={inputRef}
              id="name-prompt-input"
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
              }}
              disabled={submitting}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                onOpenChange(false);
              }}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !value.trim()}>
              {submitting && <Loader2 className="size-4 animate-spin" />}
              {submitting ? "Working…" : confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
