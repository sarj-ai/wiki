"use client";

import { FileText, Sparkles } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import {
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { LazyDocumentPolishDialog } from "@/components/wiki-document/lazy-document-polish-dialog";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";
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
const SmartCreateDialog = dynamic(
  async () => {
    const loadedModule = await import("./smart-create-dialog");
    return loadedModule.SmartCreateDialog;
  },
  { ssr: false },
);

export interface CreateFileMenuActionsProps {
  driveId: string;
  onMenuOpenChange: (open: boolean) => void;
  parentFolderId: string;
}

interface CreateType {
  defaultName: string;
  label: string;
  mimeType: string;
}

const CREATE_TYPES: readonly CreateType[] = [
  { defaultName: "Untitled Doc", label: "Doc", mimeType: MIME_DOC },
  { defaultName: "Untitled Sheet", label: "Sheet", mimeType: MIME_SHEET },
  { defaultName: "Untitled Slides", label: "Slides", mimeType: MIME_SLIDE },
  { defaultName: "Untitled Drawing", label: "Drawing", mimeType: MIME_DRAWING },
  { defaultName: "Untitled Form", label: "Form", mimeType: MIME_FORM },
  { defaultName: "New Folder", label: "Folder", mimeType: MIME_FOLDER },
];

export function CreateFileMenuActions({
  driveId,
  onMenuOpenChange,
  parentFolderId,
}: CreateFileMenuActionsProps) {
  const router = useRouter();
  const createFile = useCreateFile();
  const smartDialog = useDeferredDialog();
  const markdownDialog = useDeferredDialog();

  const [pendingType, setPendingType] = useState<CreateType | null>(null);

  const pickType = (type: CreateType) => {
    onMenuOpenChange(false);
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
      <DropdownMenuContent align="start" className="w-44">
        <DropdownMenuItem
          className="flex items-center gap-2 text-sm"
          onClick={() => {
            onMenuOpenChange(false);
            smartDialog.onOpenChange(true);
          }}
        >
          <Sparkles className="size-4" />
          Smart create…
        </DropdownMenuItem>
        <DropdownMenuItem
          className="flex items-center gap-2 text-sm"
          onClick={() => {
            onMenuOpenChange(false);
            markdownDialog.onOpenChange(true);
          }}
        >
          <FileText className="size-4" />
          Import Markdown…
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
      {smartDialog.mounted ? (
        <SmartCreateDialog
          invokingDriveId={driveId}
          invokingFolderId={parentFolderId}
          onOpenChange={smartDialog.onOpenChange}
          open={smartDialog.open}
        />
      ) : null}
      {markdownDialog.mounted ? (
        <LazyDocumentPolishDialog
          onOpenChange={markdownDialog.onOpenChange}
          open={markdownDialog.open}
          source={{
            initialDestination: {
              driveId,
              driveName: "Current workspace",
              folderId: parentFolderId,
              folderPath: ["Current folder"],
            },
            kind: "markdown",
          }}
        />
      ) : null}
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
