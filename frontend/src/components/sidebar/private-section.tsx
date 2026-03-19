"use client";

import { Plus, NotebookPen, Loader2, ChevronRight } from "lucide-react";
import {
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { PageTree } from "./page-tree";
import type { Page } from "@/types";

interface PrivateSectionProps {
  privatePages: Page[];
  collapsedSections: Set<string>;
  hydrated: boolean;
  toggleSection: (id: string) => void;
  activePageId: string | null;
  handleAddPrivatePage: () => void;
  handleAddChild: (parentId: string) => void;
  setDeletingPageId: (id: string | null) => void;
  handleRename: (pageId: string, newTitle: string) => void;
  handleChangeIcon: (pageId: string, icon: string | null) => void;
  handleMove: (pageId: string, newParentId: string | null, newIndex: number) => void;
  renamingPageId: string | null;
  handleRenamingDone: () => void;
  isTreeLoading: boolean;
  addingTo: string | null;
  addingChildParentId: string | null;
}

export function PrivateSection({
  privatePages,
  collapsedSections,
  hydrated,
  toggleSection,
  activePageId,
  handleAddPrivatePage,
  handleAddChild,
  setDeletingPageId,
  handleRename,
  handleChangeIcon,
  handleMove,
  renamingPageId,
  handleRenamingDone,
  isTreeLoading,
  addingTo,
  addingChildParentId,
}: PrivateSectionProps) {
  return (
    <Collapsible
      open={!hydrated || !collapsedSections.has("private")}
      onOpenChange={() => toggleSection("private")}
    >
      <SidebarGroup>
        <SidebarGroupLabel asChild className="text-sm font-semibold text-sidebar-foreground">
          <div className="flex items-center">
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="mr-1 inline-flex shrink-0 items-center justify-center rounded-sm p-0.5 hover:bg-sidebar-accent transition-colors"
                aria-label="Toggle private pages"
              >
                <ChevronRight className={`size-3 transition-transform ${!hydrated || !collapsedSections.has("private") ? "rotate-90" : ""}`} />
              </button>
            </CollapsibleTrigger>
            <NotebookPen className="mr-1 size-3.5" />
            <span className="truncate">My Drafts</span>
          </div>
        </SidebarGroupLabel>
        <SidebarGroupAction onClick={handleAddPrivatePage} title="Add private page" disabled={addingTo !== null}>
          {addingTo === "private" ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
        </SidebarGroupAction>
        <CollapsibleContent>
          <SidebarGroupContent className="pl-5">
            <PageTree
              pages={privatePages}
              activePageId={activePageId}
              onAddChild={handleAddChild}
              onDelete={setDeletingPageId}
              onRename={handleRename}
              onChangeIcon={handleChangeIcon}
              onMove={handleMove}
              renamingPageId={renamingPageId}
              onRenamingDone={handleRenamingDone}
              isLoading={isTreeLoading}
              addingChildParentId={addingChildParentId}
            />
          </SidebarGroupContent>
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}
