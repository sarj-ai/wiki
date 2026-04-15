"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
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

  // Reset the field whenever the dialog opens so consecutive uses don't
  // carry over a stale value from the previous run.
  useEffect(() => {
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
      <DialogContent className="max-w-sm">
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
              id="name-prompt-input"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoFocus
              disabled={submitting}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
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
