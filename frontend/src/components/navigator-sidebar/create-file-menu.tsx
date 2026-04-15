"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  MIME_DOC,
  MIME_DRAWING,
  MIME_FOLDER,
  MIME_FORM,
  MIME_SHEET,
  MIME_SLIDE,
} from "@/types/navigator";
import { useCreateFile } from "@/hooks/use-navigator-mutations";
import { isGoogleWorkspaceType, renderMimeIcon } from "@/lib/file-icons";

interface CreateFileMenuProps {
  parentFolderId: string;
  // Compact variant: icon-only trigger meant to sit inline in a sidebar
  // row. Falls back to a labeled button otherwise.
  compact?: boolean;
}

const CREATE_TYPES: { mimeType: string; label: string; defaultName: string }[] = [
  { mimeType: MIME_DOC, label: "Doc", defaultName: "Untitled Doc" },
  { mimeType: MIME_SHEET, label: "Sheet", defaultName: "Untitled Sheet" },
  { mimeType: MIME_SLIDE, label: "Slides", defaultName: "Untitled Slides" },
  { mimeType: MIME_DRAWING, label: "Drawing", defaultName: "Untitled Drawing" },
  { mimeType: MIME_FORM, label: "Form", defaultName: "Untitled Form" },
  { mimeType: MIME_FOLDER, label: "Folder", defaultName: "New Folder" },
];

export function CreateFileMenu({ parentFolderId, compact = true }: CreateFileMenuProps) {
  const router = useRouter();
  const createFile = useCreateFile();
  const [open, setOpen] = useState(false);

  const handleCreate = async (type: (typeof CREATE_TYPES)[number]) => {
    setOpen(false);
    const name = window.prompt(`Name for new ${type.label}`, type.defaultName);
    if (!name || name.trim() === "") return;
    try {
      const file = await createFile.mutateAsync({
        parentFolderId,
        name: name.trim(),
        mimeType: type.mimeType,
      });
      // Folders stay in the sidebar; files open immediately inside the
      // navigator viewer so the user can start editing right away.
      if (isGoogleWorkspaceType(file.mimeType)) {
        router.push(`/d/${encodeURIComponent(file.id)}`);
      }
      toast.success(`${type.label} created`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Create failed";
      toast.error(message);
    }
  };

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          onClick={(e) => e.stopPropagation()}
          className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-5 shrink-0 items-center justify-center rounded outline-none"
          aria-label="Create item"
        >
          <Plus className={compact ? "size-3" : "size-3.5"} />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-44">
        {CREATE_TYPES.map((type) => (
          <DropdownMenuItem
            key={type.mimeType}
            onClick={() => handleCreate(type)}
            className="flex items-center gap-2 text-sm"
          >
            {renderMimeIcon(type.mimeType, "size-4")}
            {type.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
