"use client";

import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useIsClient } from "@/hooks/use-is-client";
import { cn } from "@/lib/utils";

interface EmojiPickerPopoverProps {
  children: React.ReactElement;

  onChange: (next: null | string) => Promise<void> | void;

  value: string | undefined;
}

interface EmojiPickerBodyProps {
  onSelect: (emoji: string) => void;
  theme: "dark" | "light";
}

// One static class per grid column so Tailwind can compile the stagger.
const SKELETON_STAGGER = [
  "[animation-delay:0ms]",
  "[animation-delay:30ms]",
  "[animation-delay:60ms]",
  "[animation-delay:90ms]",
  "[animation-delay:120ms]",
  "[animation-delay:150ms]",
  "[animation-delay:180ms]",
  "[animation-delay:210ms]",
  "[animation-delay:240ms]",
];

function EmojiPickerSkeleton() {
  return (
    <div className="bg-card border-border flex h-[435px] w-[352px] flex-col gap-3 rounded-lg border p-3 shadow-sm">
      <div className="bg-muted h-8 w-full animate-pulse rounded-md" />

      <div className="flex items-center gap-1">
        {Array.from({ length: 9 }).map((_, i) => (
          <div className="bg-muted size-6 animate-pulse rounded-sm" key={i} />
        ))}
      </div>

      <div className="grid flex-1 grid-cols-9 gap-1 overflow-hidden">
        {Array.from({ length: 54 }).map((_, i) => (
          <div
            className={cn(
              "bg-muted/60 aspect-square animate-pulse rounded-sm",
              SKELETON_STAGGER[i % SKELETON_STAGGER.length],
            )}
            key={i}
          />
        ))}
      </div>
    </div>
  );
}

const Picker = dynamic(async () => import("@emoji-mart/react").then((m) => m.default), {
  loading: () => <EmojiPickerSkeleton />,
  ssr: false,
});

let cachedData: null | Promise<unknown> = null;
async function loadEmojiData(): Promise<unknown> {
  cachedData ??= import("@emoji-mart/data").then((m: { default: unknown }) => m.default);
  return cachedData;
}

function EmojiPickerBody({ onSelect, theme }: EmojiPickerBodyProps) {
  return (
    <Picker
      autoFocus
      data={loadEmojiData}
      maxFrequentRows={2}
      onEmojiSelect={(picked: { native?: string; id?: string }) => {
        const value = picked.native ?? picked.id ?? null;
        if (value) onSelect(value);
      }}
      previewPosition="none"
      skinTonePosition="none"
      theme={theme}
    />
  );
}

export function EmojiPickerPopover({ children, onChange, value }: EmojiPickerPopoverProps) {
  const { resolvedTheme } = useTheme();
  const mounted = useIsClient();
  const theme = mounted && resolvedTheme === "dark" ? "dark" : "light";
  const [pending, startChange] = useTransition();

  const handleChange = (next: null | string) => {
    if (pending) return;
    startChange(async () => {
      await onChange(next);
    });
  };

  return (
    <Popover>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-auto border-0 bg-transparent p-0 shadow-none"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
        }}
        side="right"
        sideOffset={8}
      >
        <EmojiPickerBody
          onSelect={(emoji) => {
            handleChange(emoji);
          }}
          theme={theme}
        />
        {value ? (
          <div className="mt-2 flex justify-end">
            <Button
              disabled={pending}
              onClick={() => {
                handleChange(null);
              }}
              size="sm"
              type="button"
              variant="outline"
            >
              Remove icon
            </Button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
