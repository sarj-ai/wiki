"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCreateFile } from "@/hooks/use-navigator-mutations";
import { isGoogleWorkspaceType, renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import {
  MIME_DOC,
  MIME_DRAWING,
  MIME_FOLDER,
  MIME_FORM,
  MIME_SHEET,
  MIME_SLIDE,
} from "@/types/navigator";

import { NamePromptDialog } from "./name-prompt-dialog";

interface CreateFileMenuProps {
  parentFolderId: string;
  // Compact variant: icon-only trigger meant to sit inline in a sidebar
  // row. Falls back to a labeled button otherwise.
  compact?: boolean;
}

interface CreateType {
  mimeType: string;
  label: string;
  defaultName: string;
}

const CREATE_TYPES: CreateType[] = [
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
  const [menuOpen, setMenuOpen] = useState(false);
  // Which type the user picked from the dropdown — the dialog for the
  // actual name input opens once `pendingType` is non-null.
  const [pendingType, setPendingType] = useState<CreateType | null>(null);

  const pickType = (type: CreateType) => {
    setMenuOpen(false);
    setPendingType(type);
  };

  const confirmCreate = async (name: string) => {
    if (!pendingType) return;
    try {
      const file = await createFile.mutateAsync({
        parentFolderId,
        name,
        mimeType: pendingType.mimeType,
      });
      // Forms have a Drive-API-level permission quirk that makes the
      // /edit URL bounce on a "You need access" gate when iframed, so
      // they get launched in a fresh tab where the user's top-level
      // Google session handles the ACL check. Other Workspace types
      // open inline in the Navigator viewer.
      if (file.mimeType === MIME_FORM) {
        window.open(openUrlFor(file.mimeType, file.id), "_blank", "noopener,noreferrer");
      } else if (isGoogleWorkspaceType(file.mimeType)) {
        router.push(`/d/${encodeURIComponent(file.id)}`);
      }
      toast.success(`${pendingType.label} created`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Create failed";
      toast.error(message);
    }
  };

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); }}
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
              onClick={() => { pickType(type); }}
              className="flex items-center gap-2 text-sm"
            >
              {renderMimeIcon(type.mimeType, "size-4")}
              {type.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <NamePromptDialog
        open={pendingType !== null}
        onOpenChange={(open) => {
          if (!open) setPendingType(null);
        }}
        title={pendingType ? `New ${pendingType.label}` : ""}
        description={
          pendingType ? `Creates a new ${pendingType.label.toLowerCase()} in this folder.` : ""
        }
        defaultValue={pendingType?.defaultName ?? ""}
        confirmLabel="Create"
        onConfirm={confirmCreate}
      />
    </>
  );
}
