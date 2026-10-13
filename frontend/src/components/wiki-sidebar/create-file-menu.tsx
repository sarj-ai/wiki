"use client";

import { Plus } from "lucide-react";
import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";

const CreateFileMenuActions = dynamic(
  async () => {
    const loadedModule = await import("./create-file-menu-actions");
    return loadedModule.CreateFileMenuActions;
  },
  { ssr: false },
);

export function CreateFileMenu({
  compact = true,
  driveId,
  parentFolderId,
  parentName,
}: CreateFileMenuProps) {
  const menu = useDeferredDialog();
  return (
    <DropdownMenu onOpenChange={menu.onOpenChange} open={menu.open}>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={parentName ? `Create item in ${parentName}` : "Create item"}
          className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-5 shrink-0 rounded-sm"
          onClick={(event) => {
            event.stopPropagation();
          }}
          size="icon-xs"
          variant="ghost"
        >
          <Plus className={compact ? "size-3" : "size-3.5"} />
        </Button>
      </DropdownMenuTrigger>
      {menu.mounted ? (
        <CreateFileMenuActions
          driveId={driveId}
          onMenuOpenChange={menu.onOpenChange}
          parentFolderId={parentFolderId}
        />
      ) : null}
    </DropdownMenu>
  );
}

interface CreateFileMenuProps {
  compact?: boolean;
  driveId: string;
  parentFolderId: string;
  parentName?: string;
}
