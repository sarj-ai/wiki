"use client";

import { Plus, NotebookPen, Loader2, ChevronRight, Upload } from "lucide-react";
import {
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
  starredIds?: Set<string>;
  onToggleStar?: (pageId: string) => void;
  onImport?: (pageId: string) => void;
  onImportToDrafts?: () => void;
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
  starredIds,
  onToggleStar,
  onImport,
  onImportToDrafts,
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
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarGroupAction title="Add to My Drafts">
              {addingTo === "private" ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            </SidebarGroupAction>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right" align="start">
            <DropdownMenuItem onClick={handleAddPrivatePage}>
              <Plus className="mr-2 size-4" />
              New page
            </DropdownMenuItem>
            {onImportToDrafts && (
              <DropdownMenuItem onClick={onImportToDrafts}>
                <Upload className="mr-2 size-4" />
                Import pages
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
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
              starredIds={starredIds}
              onToggleStar={onToggleStar}
              onImport={onImport}
            />
          </SidebarGroupContent>
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}
