"use client";

import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useCommandPaletteState } from "@/hooks/use-command-palette";

import { useSidebarNavigationIntent } from "./use-sidebar-navigation";

export function DriveSearchBox() {
  const { setOpen } = useCommandPaletteState();
  const captureNavigation = useSidebarNavigationIntent();
  return (
    <Button
      aria-label="Open search"
      className="text-muted-foreground hover:bg-accent/40 w-full justify-start text-xs font-normal"
      onClick={(event) => {
        setOpen(true, event.currentTarget, captureNavigation());
      }}
      size="sm"
      variant="outline"
    >
      <Search className="size-3.5 shrink-0" />
      <span className="flex-1 text-left">Search Drive…</span>
      <kbd className="bg-muted text-muted-foreground rounded-sm border px-1 py-0.5 font-mono text-[10px]">
        ⌘K
      </kbd>
    </Button>
  );
}
