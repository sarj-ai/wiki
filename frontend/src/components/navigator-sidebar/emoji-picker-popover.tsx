"use client";

import { useTheme } from "next-themes";
import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useIsClient } from "@/hooks/use-is-client";

/**
 * Skeleton placeholder that matches the live emoji-mart picker's
 * footprint (~352×435px) so there's no layout jump when the real
 * component hydrates. Uses the sidebar card tokens so it theme-flips
 * with light/dark, and a subtle pulse so the loading state reads as
 * "working" rather than "broken".
 */
function EmojiPickerSkeleton() {
  return (
    <div className="bg-card border-border flex h-[435px] w-[352px] flex-col gap-3 rounded-lg border p-3 shadow-sm">
      {/* Search bar placeholder */}
      <div className="bg-muted h-8 w-full animate-pulse rounded-md" />
      {/* Category row placeholder */}
      <div className="flex items-center gap-1">
        {Array.from({ length: 9 }).map((_, i) => (
          <div key={i} className="bg-muted size-6 animate-pulse rounded-sm" />
        ))}
      </div>
      {/* Emoji grid placeholder — 8 columns × 6 rows matches emoji-mart */}
      <div className="grid flex-1 grid-cols-9 gap-1 overflow-hidden">
        {Array.from({ length: 54 }).map((_, i) => (
          <div
            key={i}
            className="bg-muted/60 aspect-square animate-pulse rounded-sm"
            style={{ animationDelay: `${(i % 9) * 30}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

// Lazy-load the actual Picker — it pulls in ~2 MB of emoji data, so
// paying for it up-front on every page load would hurt first paint
// for users who never edit an icon. `ssr: false` keeps it out of the
// server bundle too; the picker is pure client-side.
const Picker = dynamic(() => import("@emoji-mart/react").then((m) => m.default), {
  ssr: false,
  loading: () => <EmojiPickerSkeleton />,
});

// Emoji-mart's dataset is the biggest dep — also dynamically imported
// so it's only fetched alongside the picker. Cache the in-flight
// Promise (not the resolved value) so two concurrent calls can't both
// trigger the dynamic import.
let cachedData: Promise<unknown> | null = null;
function loadEmojiData(): Promise<unknown> {
  cachedData ??= import("@emoji-mart/data").then((m: { default: unknown }) => m.default);
  return cachedData;
}

interface EmojiPickerPopoverProps {
  /** Current icon value — shown in the trigger button. */
  value: string | null;
  /** Called with the new emoji, or ``null`` if the user clicks Remove. */
  onChange: (next: string | null) => void | Promise<void>;
  /** Trigger element to wrap (the file/folder icon in the sidebar row). */
  children: React.ReactElement;
}

export function EmojiPickerPopover({ value, onChange, children }: EmojiPickerPopoverProps) {
  const { resolvedTheme } = useTheme();
  const mounted = useIsClient();
  const theme = mounted && resolvedTheme === "dark" ? "dark" : "light";

  return (
    <Popover>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={8}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
        onOpenAutoFocus={(e) => { e.preventDefault(); }}
      >
        {/* emoji-mart provides its own background + shadow — wrapping
            it in a transparent PopoverContent avoids a double-card
            look while keeping the click-outside-to-close behavior. */}
        <EmojiPickerBody
          theme={theme}
          onSelect={(emoji) => {
            void onChange(emoji);
          }}
        />
        {value && (
          <div className="mt-2 flex justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                void onChange(null);
              }}
            >
              Remove icon
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

interface EmojiPickerBodyProps {
  theme: "light" | "dark";
  onSelect: (emoji: string) => void;
}

function EmojiPickerBody({ theme, onSelect }: EmojiPickerBodyProps) {
  return (
    <Picker
      data={loadEmojiData}
      theme={theme}
      // Focus the picker's search input as soon as it mounts so the
      // user can start typing to filter emojis without a second click.
      // We prevented radix's auto-focus in the PopoverContent so radix
      // doesn't grab focus first and land it on the wrong element.
      autoFocus
      onEmojiSelect={(picked: { native?: string; id?: string }) => {
        const value = picked.native ?? picked.id ?? null;
        if (value) onSelect(value);
      }}
      previewPosition="none"
      skinTonePosition="none"
      maxFrequentRows={2}
    />
  );
}
