"use client";

import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import { useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useIsClient } from "@/hooks/use-is-client";

interface EmojiPickerPopoverProps {
  value: string | undefined;

  onChange: (next: string | null) => void | Promise<void>;

  children: React.ReactElement;
}

interface EmojiPickerBodyProps {
  theme: "light" | "dark";
  onSelect: (emoji: string) => void;
}

function EmojiPickerSkeleton() {
  return (
    <div className="bg-card border-border flex h-[435px] w-[352px] flex-col gap-3 rounded-lg border p-3 shadow-sm">
      <div className="bg-muted h-8 w-full animate-pulse rounded-md" />

      <div className="flex items-center gap-1">
        {Array.from({ length: 9 }).map((_, i) => (
          <div key={i} className="bg-muted size-6 animate-pulse rounded-sm" />
        ))}
      </div>

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

const Picker = dynamic(() => import("@emoji-mart/react").then((m) => m.default), {
  ssr: false,
  loading: () => <EmojiPickerSkeleton />,
});

let cachedData: Promise<unknown> | null = null;
function loadEmojiData(): Promise<unknown> {
  cachedData ??= import("@emoji-mart/data").then((m: { default: unknown }) => m.default);
  return cachedData;
}

function EmojiPickerBody({ theme, onSelect }: EmojiPickerBodyProps) {
  return (
    <Picker
      data={loadEmojiData}
      theme={theme}
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

export function EmojiPickerPopover({ value, onChange, children }: EmojiPickerPopoverProps) {
  const { resolvedTheme } = useTheme();
  const mounted = useIsClient();
  const theme = mounted && resolvedTheme === "dark" ? "dark" : "light";
  const [pending, startChange] = useTransition();

  const handleChange = (next: string | null) => {
    if (pending) return;
    startChange(async () => {
      await onChange(next);
    });
  };

  return (
    <Popover>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={8}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
        }}
      >
        <EmojiPickerBody
          theme={theme}
          onSelect={(emoji) => {
            handleChange(emoji);
          }}
        />
        {value && (
          <div className="mt-2 flex justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pending}
              onClick={() => {
                handleChange(null);
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
