"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, useTransition } from "react";

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

export function NamePromptDialog({
  confirmLabel = "Create",
  defaultValue = "",
  description,
  onConfirm,
  onOpenChange,
  open,
  title,
}: NamePromptDialogProps) {
  const [value, setValue] = useState(defaultValue);
  const [submitting, startSubmit] = useTransition();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const promptOwner = useRef<{ submitted: boolean } | null>(null);
  const focusFrame = useRef<null | number>(null);

  useLayoutEffect(() => {
    const owner = { submitted: false };
    promptOwner.current = open ? owner : null;
    return () => {
      promptOwner.current = null;
      if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
      focusFrame.current = null;
    };
  }, [open]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Prop changes intentionally reset controlled input state.
    if (open) setValue(defaultValue);
  }, [open, defaultValue]);

  const handleSubmit = () => {
    const owner = promptOwner.current;
    if (!owner || owner.submitted || submitting) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    owner.submitted = true;
    startSubmit(async () => {
      try {
        await onConfirm(trimmed);
        if (promptOwner.current === owner) onOpenChange(false);
      } finally {
        owner.submitted = false;
      }
    });
  };

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        className="max-w-sm"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          const owner = promptOwner.current;
          if (!owner) return;
          if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
          focusFrame.current = requestAnimationFrame(() => {
            focusFrame.current = null;
            if (promptOwner.current !== owner) return;
            const el = inputRef.current;
            if (!el) return;
            el.focus();
            el.select();
          });
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="name-prompt-input">Name</Label>
            <Input
              disabled={submitting}
              id="name-prompt-input"
              maxLength={255}
              onChange={(e) => {
                setValue(e.target.value);
              }}
              ref={inputRef}
              value={value}
            />
          </div>
          <DialogFooter>
            <Button
              disabled={submitting}
              onClick={() => {
                onOpenChange(false);
              }}
              type="button"
              variant="outline"
            >
              Cancel
            </Button>
            <Button disabled={submitting || !value.trim()} type="submit">
              {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
              {submitting ? "Working…" : confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface NamePromptDialogProps {
  confirmLabel?: string;
  defaultValue?: string;
  description?: string;
  onConfirm: (value: string) => Promise<void> | void;

  onOpenChange: (open: boolean) => void;
  open: boolean;
  title: string;
}
