"use client";

import dynamic from "next/dynamic";

import { useCommandPaletteHotkey, useCommandPaletteMounted } from "@/hooks/use-command-palette";

const CommandPalette = dynamic(
  async () => import("./command-palette").then((m) => m.CommandPalette),
  {
    ssr: false,
  },
);

export function CommandPaletteMount() {
  useCommandPaletteHotkey();
  const mounted = useCommandPaletteMounted();

  if (!mounted) return null;
  return <CommandPalette />;
}
