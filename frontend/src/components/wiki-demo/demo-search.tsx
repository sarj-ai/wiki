"use client";

import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { DialogClose } from "@/components/ui/dialog";
import { renderMimeIcon } from "@/lib/file-icons";

import { DEMO_FILES, type DemoFile } from "./demo-data";
import { DEMO_SEARCH_TEXT } from "./demo-search-text";

export function DemoSearch({
  onOpenChange,
  onSelect,
  open,
}: {
  onOpenChange: (open: boolean) => void;
  onSelect: (file: DemoFile) => void;
  open: boolean;
}) {
  return (
    <CommandDialog
      description="Search file names and sample content. Use the arrow keys to select a result, then Enter to open it."
      onOpenChange={onOpenChange}
      open={open}
      showCloseButton={false}
      title="Search sample files"
    >
      <div className="pr-11">
        <CommandInput
          aria-label="Search sample files"
          placeholder="Search files, people, or project details…"
        />
      </div>
      <CommandList className="max-h-[min(400px,65dvh)]">
        <CommandEmpty>
          <p className="font-medium">No files found</p>
          <p className="text-muted-foreground mt-1 text-xs">Try “budget”, “launch”, or “Maya”.</p>
        </CommandEmpty>
        <CommandGroup heading="Northstar · sample workspace">
          {DEMO_FILES.map((file) => (
            <CommandItem
              key={file.id}
              keywords={[
                file.name,
                file.description,
                file.keywords,
                DEMO_SEARCH_TEXT[file.id] ?? "",
              ]}
              onSelect={() => onSelect(file)}
              value={file.id}
            >
              <span className="flex min-w-0 flex-1 items-start gap-3 px-1">
                <span aria-hidden className="pt-0.5">
                  {renderMimeIcon(file.mimeType, "size-4")}
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium">{file.name}</span>
                  <span className="text-muted-foreground mt-0.5 block text-xs">
                    {file.description}
                  </span>
                </span>
              </span>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
      <div className="text-muted-foreground border-t px-4 py-2 text-xs">
        ↑ ↓ to navigate <span className="mx-2">↵ to open</span> Esc to close
      </div>
      <DialogClose asChild>
        <Button
          aria-label="Close search"
          className="absolute top-1 right-1 z-10"
          size="icon-lg"
          variant="ghost"
        >
          <X aria-hidden />
        </Button>
      </DialogClose>
    </CommandDialog>
  );
}
