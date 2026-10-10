"use client";

import { MoreHorizontal } from "lucide-react";
import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";

const ItemMenuActions = dynamic(
  async () => {
    const loadedModule = await import("./item-menu-actions");
    return loadedModule.ItemMenuActions;
  },
  { ssr: false },
);

export function ItemMenu(props: ItemMenuProps) {
  const menu = useDeferredDialog();
  return (
    <DropdownMenu onOpenChange={menu.onOpenChange} open={menu.open}>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`Actions for ${props.currentName}`}
          className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-5 shrink-0 rounded-sm"
          onClick={(event) => {
            event.stopPropagation();
          }}
          size="icon-xs"
          variant="ghost"
        >
          <MoreHorizontal className="size-3" />
        </Button>
      </DropdownMenuTrigger>
      {menu.mounted ? <ItemMenuActions {...props} /> : null}
    </DropdownMenu>
  );
}

export interface ItemMenuProps {
  currentName: string;
  driveId: string | undefined;
  fileId: string;
  isFolder: boolean;
  parentFolderId: string | undefined;
}
