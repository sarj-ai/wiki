"use client";

import { Search } from "lucide-react";

import { useCommandPaletteState } from "@/hooks/use-command-palette";

/**
 * Spotlight-style trigger in the sidebar header. Clicking opens the
 * command palette; the real search UI lives in
 * ``components/navigator-search/command-palette.tsx``. The kbd hint
 * uses ``⌘K`` on macOS and ``Ctrl K`` elsewhere — we keep the macOS
 * glyph here because the Navigator is a Sarj-internal tool on
 * predominantly-Mac laptops; the hotkey binding itself handles both
 * modifier keys in ``use-command-palette.ts``.
 */
export function DriveSearchBox() {
  const { setOpen } = useCommandPaletteState();
  return (
    <button
      type="button"
      onClick={() => { setOpen(true); }}
      className="border-input bg-background hover:bg-accent/40 text-muted-foreground flex h-8 w-full items-center gap-2 rounded-md border px-2 text-xs outline-none"
      aria-label="Open search"
    >
      <Search className="size-3.5 shrink-0" />
      <span className="flex-1 text-left">Search Drive…</span>
      <kbd className="bg-muted text-muted-foreground rounded border px-1 py-0.5 font-mono text-[10px]">
        ⌘K
      </kbd>
    </button>
  );
}
