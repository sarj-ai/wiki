"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState, useTransition } from "react";

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
  const [submitting, startSubmit] = useTransition();
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (open) setValue(defaultValue);
  }, [open, defaultValue]);

  const handleSubmit = () => {
    if (submitting) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    startSubmit(async () => {
      await onConfirm(trimmed);
      onOpenChange(false);
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-sm"
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
            handleSubmit();
          }}
          className="space-y-3"
        >
          <div className="space-y-1.5">
            <Label htmlFor="name-prompt-input">Name</Label>
            <Input
              ref={inputRef}
              id="name-prompt-input"
              value={value}
              maxLength={255}
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
