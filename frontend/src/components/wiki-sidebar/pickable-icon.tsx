"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { EmojiPickerPopover } from "./emoji-picker-popover";

interface PickableIconProps {
  ariaLabel: string;
  children: ReactNode;
  dimmed?: boolean;
  onChange: (next: null | string) => Promise<void>;
  pickable: boolean;
  value: string | undefined;
}

export function PickableIcon({
  ariaLabel,
  children,
  dimmed,
  onChange,
  pickable,
  value,
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
    <EmojiPickerPopover onChange={onChange} value={value}>
      <Button
        aria-label={ariaLabel}
        className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-6 shrink-0 cursor-pointer transition-colors"
        size="icon-xs"
        variant="ghost"
      >
        {children}
      </Button>
    </EmojiPickerPopover>
  );
}
