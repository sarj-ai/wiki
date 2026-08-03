"use client";

import { Plus, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useCreateFile } from "@/hooks/use-wiki-mutations";
import { isGoogleWorkspaceType, renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { runWithErrorToast } from "@/lib/mutation-toast";
import {
  MIME_DOC,
  MIME_DRAWING,
  MIME_FOLDER,
  MIME_FORM,
  MIME_SHEET,
  MIME_SLIDE,
} from "@/types/wiki";

import { NamePromptDialog } from "./name-prompt-dialog";
import { SmartCreateDialog } from "./smart-create-dialog";

interface CreateFileMenuProps {
  compact?: boolean;
  driveId: string;
  parentFolderId: string;
}

interface CreateType {
  defaultName: string;
  label: string;
  mimeType: string;
}

const CREATE_TYPES: CreateType[] = [
  { defaultName: "Untitled Doc", label: "Doc", mimeType: MIME_DOC },
  { defaultName: "Untitled Sheet", label: "Sheet", mimeType: MIME_SHEET },
  { defaultName: "Untitled Slides", label: "Slides", mimeType: MIME_SLIDE },
  { defaultName: "Untitled Drawing", label: "Drawing", mimeType: MIME_DRAWING },
  { defaultName: "Untitled Form", label: "Form", mimeType: MIME_FORM },
  { defaultName: "New Folder", label: "Folder", mimeType: MIME_FOLDER },
];

export function CreateFileMenu({ compact = true, driveId, parentFolderId }: CreateFileMenuProps) {
  const router = useRouter();
  const createFile = useCreateFile();
  const [menuOpen, setMenuOpen] = useState(false);
  const [smartOpen, setSmartOpen] = useState(false);

  const [pendingType, setPendingType] = useState<CreateType | null>(null);

  const pickType = (type: CreateType) => {
    setMenuOpen(false);
    setPendingType(type);
  };

  const confirmCreate = async (name: string) => {
    if (!pendingType) return;
    await runWithErrorToast(async () => {
      const file = await createFile.mutateAsync({
        mimeType: pendingType.mimeType,
        name,
        parentFolderId,
      });

      if (file.mimeType === MIME_FORM) {
        window.open(openUrlFor(file.mimeType, file.id), "_blank", "noopener,noreferrer");
      } else if (isGoogleWorkspaceType(file.mimeType)) {
        router.push(`/d/${encodeURIComponent(file.id)}`);
      }
      toast.success(`${pendingType.label} created`);
    }, "Create failed");
  };

  return (
    <>
      <DropdownMenu onOpenChange={setMenuOpen} open={menuOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            aria-label="Create item"
            className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-5 shrink-0 rounded-sm"
            onClick={(e) => {
              e.stopPropagation();
            }}
            size="icon-xs"
            variant="ghost"
          >
            <Plus className={compact ? "size-3" : "size-3.5"} />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-44">
          <DropdownMenuItem
            className="flex items-center gap-2 text-sm"
            onClick={() => {
              setMenuOpen(false);
              setSmartOpen(true);
            }}
          >
            <Sparkles className="size-4" />
            Smart create…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          {CREATE_TYPES.map((type) => (
            <DropdownMenuItem
              className="flex items-center gap-2 text-sm"
              key={type.mimeType}
              onClick={() => {
                pickType(type);
              }}
            >
              {renderMimeIcon(type.mimeType, "size-4")}
              {type.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <SmartCreateDialog
        invokingDriveId={driveId}
        invokingFolderId={parentFolderId}
        onOpenChange={setSmartOpen}
        open={smartOpen}
      />
      <NamePromptDialog
        confirmLabel="Create"
        defaultValue={pendingType?.defaultName ?? ""}
        description={
          pendingType ? `Creates a new ${pendingType.label.toLowerCase()} in this folder.` : ""
        }
        onConfirm={confirmCreate}
        onOpenChange={(open) => {
          if (!open) setPendingType(null);
        }}
        open={pendingType !== null}
        title={pendingType ? `New ${pendingType.label}` : ""}
      />
    </>
  );
}
