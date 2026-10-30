"use client";

import dynamic from "next/dynamic";

import { useCommandPaletteHotkey, useCommandPaletteMounted } from "@/hooks/use-command-palette";

const CommandPalette = dynamic(
  async () => {
    const { CommandPalette: Palette } = await import("./command-palette");
    return Palette;
  },
  { ssr: false },
);

export function CommandPaletteMount() {
  useCommandPaletteHotkey();
  const mounted = useCommandPaletteMounted();

  if (!mounted) return null;
  return <CommandPalette />;
}
