"use client";

import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { SmilePlus } from "lucide-react";

const Picker = dynamic(() => import("@emoji-mart/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="h-[350px] w-[352px]" />,
});

import data from "@emoji-mart/data";

interface EmojiPickerProps {
  currentEmoji?: string | null;
  onEmojiSelect: (emoji: string) => void;
  onRemove?: () => void;
  /** Controlled open state */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: React.ReactNode;
}

export function EmojiPicker({
  currentEmoji,
  onEmojiSelect,
  onRemove,
  open,
  onOpenChange,
  children,
}: EmojiPickerProps) {
  const { resolvedTheme } = useTheme();

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        {children ?? (
          <button
            type="button"
            className="flex size-8 items-center justify-center rounded-md text-lg hover:bg-accent transition-colors"
            title="Pick icon"
          >
            {currentEmoji || <SmilePlus className="size-4 text-muted-foreground" />}
          </button>
        )}
      </PopoverTrigger>
      <PopoverContent
        className="w-auto p-0 border-none shadow-lg"
        side="right"
        align="start"
        sideOffset={8}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <Picker
          data={data}
          theme={resolvedTheme === "dark" ? "dark" : "light"}
          onEmojiSelect={(emoji: { native: string }) => {
            onEmojiSelect(emoji.native);
          }}
          previewPosition="none"
          skinTonePosition="search"
          maxFrequentRows={2}
        />
        {currentEmoji && onRemove && (
          <div className="border-t px-3 py-2">
            <button
              type="button"
              onClick={onRemove}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Remove icon
            </button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

interface EmojiPickerDialogProps {
  currentEmoji?: string | null;
  onEmojiSelect: (emoji: string) => void;
  onRemove?: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EmojiPickerDialog({
  currentEmoji,
  onEmojiSelect,
  onRemove,
  open,
  onOpenChange,
}: EmojiPickerDialogProps) {
  const { resolvedTheme } = useTheme();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="w-auto max-w-none p-0 border-none shadow-lg gap-0"
        showCloseButton={false}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <DialogTitle className="sr-only">Pick an icon</DialogTitle>
        <Picker
          data={data}
          theme={resolvedTheme === "dark" ? "dark" : "light"}
          onEmojiSelect={(emoji: { native: string }) => {
            onEmojiSelect(emoji.native);
          }}
          previewPosition="none"
          skinTonePosition="search"
          maxFrequentRows={2}
        />
        {currentEmoji && onRemove && (
          <div className="border-t px-3 py-2">
            <button
              type="button"
              onClick={onRemove}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Remove icon
            </button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
