"use client";

import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { useTransition } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";
import { useIsClient } from "@/hooks/use-is-client";
import { useSetDocIcon, useSetFolderIcon } from "@/hooks/use-wiki-mutations";
import { runWithErrorToast } from "@/lib/mutation-toast";

export interface IconTarget {
  id: string;
  kind: "doc" | "folder";
}

type IconChange = (next: null | string) => Promise<void> | void;
type IconChangeProps =
  { onChange: IconChange; target?: never } | { onChange?: never; target: IconTarget };

type EmojiPickerPopoverProps = IconChangeProps & {
  children: React.ReactElement;
  value: string | undefined;
};

interface EmojiPickerBodyProps {
  onSelect: (emoji: string) => void;
  theme: "dark" | "light";
}

function EmojiPickerSkeleton() {
  return (
    <div className="bg-card border-border flex h-[435px] w-[352px] flex-col gap-3 rounded-lg border p-3 shadow-sm">
      <div className="bg-muted h-8 w-full animate-pulse rounded-md" />

      <div className="flex items-center gap-1">
        {Array.from({ length: 9 }, (_, i) => (
          <div className="bg-muted size-6 animate-pulse rounded-sm" key={i} />
        ))}
      </div>

      <div className="grid flex-1 grid-cols-9 gap-1 overflow-hidden">
        {Array.from({ length: 54 }, (_, i) => (
          <div
            className="bg-muted/60 aspect-square animate-pulse rounded-sm"
            key={i}
            style={{ animationDelay: `${(i % 9) * 30}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

const Picker = dynamic(
  async () => {
    const { default: EmojiPicker } = await import("@emoji-mart/react");
    return EmojiPicker;
  },
  { loading: () => <EmojiPickerSkeleton />, ssr: false },
);

let cachedData: null | Promise<unknown> = null;
export function EmojiPickerPopover(props: EmojiPickerPopoverProps) {
  const picker = useDeferredDialog();
  return (
    <Popover onOpenChange={picker.onOpenChange} open={picker.open}>
      <PopoverTrigger asChild>{props.children}</PopoverTrigger>
      {picker.mounted ? <IconPickerContent {...props} /> : null}
    </Popover>
  );
}

function IconPickerContent({
  onChange,
  target,
  value,
}: IconChangeProps & { value: string | undefined }) {
  if (target) {
    return target.kind === "doc" ? (
      <DocIconPicker fileId={target.id} value={value} />
    ) : (
      <FolderIconPicker folderId={target.id} value={value} />
    );
  }
  return <EmojiPickerContent onChange={onChange} value={value} />;
}

function DocIconPicker({ fileId, value }: { fileId: string; value: string | undefined }) {
  const mutation = useSetDocIcon();
  return (
    <EmojiPickerContent
      onChange={async (icon) => {
        await runWithErrorToast(async () => {
          await mutation.mutateAsync({ driveId: fileId, icon });
        }, "Failed to update icon");
      }}
      value={value}
    />
  );
}

function FolderIconPicker({ folderId, value }: { folderId: string; value: string | undefined }) {
  const mutation = useSetFolderIcon();
  return (
    <EmojiPickerContent
      onChange={async (icon) => {
        await runWithErrorToast(async () => {
          await mutation.mutateAsync({ folderDriveId: folderId, icon });
        }, "Failed to update icon");
      }}
      value={value}
    />
  );
}

function EmojiPickerContent({
  onChange,
  value,
}: {
  onChange: IconChange;
  value: string | undefined;
}) {
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
    <PopoverContent
      align="start"
      className="w-auto border-0 bg-transparent p-0 shadow-none"
      onOpenAutoFocus={(event) => {
        event.preventDefault();
      }}
      side="right"
      sideOffset={8}
    >
      <EmojiPickerBody onSelect={handleChange} theme={theme} />
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
  );
}

async function loadEmojiData(): Promise<unknown> {
  cachedData ??= (async () => {
    const data: { default: unknown } = await import("@emoji-mart/data");
    return data.default;
  })();
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
