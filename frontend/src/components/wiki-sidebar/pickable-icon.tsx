"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { EmojiPickerPopover, type IconTarget } from "./emoji-picker-popover";

export function PickableIcon({
  ariaLabel,
  children,
  dimmed,
  pickable,
  target,
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
    <EmojiPickerPopover target={target} value={value}>
      <Button
        aria-label={ariaLabel}
        className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-sidebar-ring focus-visible:ring-offset-sidebar size-6 shrink-0 cursor-pointer transition-colors focus-visible:ring-2 focus-visible:ring-offset-2"
        size="icon-xs"
        variant="ghost"
      >
        {children}
      </Button>
    </EmojiPickerPopover>
  );
}

interface PickableIconProps {
  ariaLabel: string;
  children: ReactNode;
  dimmed?: boolean;
  pickable: boolean;
  target: IconTarget;
  value: string | undefined;
}
