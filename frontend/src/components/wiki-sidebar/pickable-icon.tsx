"use client";

import { type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { EmojiPickerPopover } from "./emoji-picker-popover";

interface PickableIconProps {
  pickable: boolean;
  dimmed?: boolean;
  value: string | null;
  onChange: (next: string | null) => Promise<void>;
  ariaLabel: string;
  children: ReactNode;
}

// Icon cell shared by file and folder rows: a plain decorative span when
// the row's icon can't be customised, or an EmojiPickerPopover trigger
// when it can. Hover uses the full sidebar highlight tokens
// (``--sidebar-accent`` / ``--sidebar-accent-foreground``) — the same
// pair that marks a selected row — so the pickable affordance is shared
// and both theme variants flip automatically.
export function PickableIcon({
  pickable,
  dimmed,
  value,
  onChange,
  ariaLabel,
  children,
}: PickableIconProps) {
  if (!pickable) {
    return (
      <span
        className={cn("flex size-6 shrink-0 items-center justify-center", dimmed && "opacity-50")}
      >
        {children}
      </span>
    );
  }
  return (
    <EmojiPickerPopover value={value} onChange={onChange}>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={ariaLabel}
        className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-6 shrink-0 cursor-pointer transition-colors"
      >
        {children}
      </Button>
    </EmojiPickerPopover>
  );
}
