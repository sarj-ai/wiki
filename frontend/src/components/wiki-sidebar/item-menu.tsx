"use client";

import { MoreHorizontal } from "lucide-react";
import dynamic from "next/dynamic";
import { useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { FocusReturnProvider } from "@/components/ui/focus-return";

import { useMoveDialogOutlet } from "./move-dialog-outlet";

const ItemMenuActions = dynamic(
  async () => {
    const loadedModule = await import("./item-menu-actions");
    return loadedModule.ItemMenuActions;
  },
  { ssr: false },
);

export function ItemMenu(props: ItemMenuProps) {
  const [menu, setMenu] = useState({ mounted: false, open: false });
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const moveOutlet = useMoveDialogOutlet();
  useLayoutEffect(() => {
    moveOutlet?.observeItem(props, triggerRef);
  }, [moveOutlet, props]);
  return (
    <FocusReturnProvider targetRef={triggerRef}>
      <DropdownMenu
        onOpenChange={(open) => {
          setMenu((previous) => (previous.open === open ? previous : { mounted: true, open }));
        }}
        open={menu.open}
      >
        <DropdownMenuTrigger asChild>
          <Button
            aria-label={`Actions for ${props.currentName}`}
            className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground size-5 shrink-0 rounded-sm"
            onClick={(event) => {
              event.stopPropagation();
            }}
            ref={triggerRef}
            size="icon-xs"
            variant="ghost"
          >
            <MoreHorizontal className="size-3" />
          </Button>
        </DropdownMenuTrigger>
        {menu.mounted ? (
          <ItemMenuActions
            {...props}
            {...(moveOutlet
              ? {
                  isMoveScopeActive: () => moveOutlet.hasActiveScope(props),
                  onMove: () => moveOutlet.openItem(props, triggerRef),
                }
              : {})}
            menuOpen={menu.open}
            moveInOutlet={moveOutlet?.isOpenFor(props) ?? false}
            onIdle={() => {
              setMenu((previous) =>
                previous.open || !previous.mounted ? previous : { mounted: false, open: false },
              );
            }}
          />
        ) : null}
      </DropdownMenu>
    </FocusReturnProvider>
  );
}

export interface ItemMenuProps {
  currentName: string;
  driveId: string | undefined;
  fileId: string;
  isFolder: boolean;
  parentFolderId: string | undefined;
}
