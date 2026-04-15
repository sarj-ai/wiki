"use client";

import React, { useContext } from "react";
import { Plus, Loader2, Building2, GripVertical, ChevronRight, Upload } from "lucide-react";
import {
  DndContext,
  closestCenter,
  type DragEndEvent,
  type SensorDescriptor,
  type SensorOptions,
} from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmojiPicker } from "@/components/emoji-picker";
import { PageTree } from "./page-tree";
import type { Page, Workspace } from "@/types";

// Context to pass drag listeners down to the grip handle
const WorkspaceDragContext = React.createContext<Record<string, unknown>>({});

function SortableWorkspaceItem({
  workspace,
  children,
}: {
  workspace: Workspace;
  children: React.ReactNode;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: workspace.id,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : undefined,
  };
  return (
    <div ref={setNodeRef} style={style} {...attributes}>
      <WorkspaceDragContext.Provider value={listeners ?? {}}>
        {children}
      </WorkspaceDragContext.Provider>
    </div>
  );
}

function WorkspaceDragHandle() {
  const listeners = useContext(WorkspaceDragContext);
  return (
    <button
      type="button"
      className="hover:bg-sidebar-accent mr-0.5 inline-flex shrink-0 cursor-grab items-center justify-center rounded-sm p-0.5 opacity-0 transition-all group-hover/ws:opacity-100 active:cursor-grabbing"
      aria-label="Drag to reorder"
      {...listeners}
    >
      <GripVertical className="text-muted-foreground size-3" />
    </button>
  );
}

interface WorkspaceSectionProps {
  workspaces: Workspace[];
  pagesByWorkspace: Map<string, Page[]>;
  collapsedSections: Set<string>;
  hydrated: boolean;
  toggleSection: (id: string) => void;
  activePageId: string | null;
  // Workspace management
  workspaceSensors: SensorDescriptor<SensorOptions>[];
  workspaceIds: string[];
  handleWorkspaceReorder: (event: DragEndEvent) => void;
  emojiPickerWorkspaceId: string | null;
  setEmojiPickerWorkspaceId: (id: string | null) => void;
  handleWorkspaceIcon: (workspaceId: string, icon: string | null) => void;
  // Page operations
  handleAddRootPage: (workspaceId: string) => void;
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
  movingPageId: string | null;
  starredIds?: Set<string>;
  onToggleStar?: (pageId: string) => void;
  onImport?: (pageId: string) => void;
  onImportToWorkspace?: (workspaceId: string) => void;
  onDuplicate?: (pageId: string) => void;
  onMoveToWorkspace?: (pageId: string) => void;
}

export function WorkspaceSection({
  workspaces,
  pagesByWorkspace,
  collapsedSections,
  hydrated,
  toggleSection,
  activePageId,
  workspaceSensors,
  workspaceIds,
  handleWorkspaceReorder,
  emojiPickerWorkspaceId,
  setEmojiPickerWorkspaceId,
  handleWorkspaceIcon,
  handleAddRootPage,
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
  movingPageId,
  starredIds,
  onToggleStar,
  onImport,
  onImportToWorkspace,
  onDuplicate,
  onMoveToWorkspace,
}: WorkspaceSectionProps) {
  return (
    <DndContext
      sensors={workspaceSensors}
      collisionDetection={closestCenter}
      onDragEnd={handleWorkspaceReorder}
    >
      <SortableContext items={workspaceIds} strategy={verticalListSortingStrategy}>
        {workspaces.map((workspace) => (
          <SortableWorkspaceItem key={workspace.id} workspace={workspace}>
            <Collapsible
              open={!hydrated || !collapsedSections.has(workspace.id)}
              onOpenChange={() => toggleSection(workspace.id)}
            >
              <SidebarGroup>
                <SidebarGroupLabel
                  asChild
                  className="text-sidebar-foreground text-sm font-semibold"
                >
                  <div className="group/ws flex items-center">
                    <WorkspaceDragHandle />
                    <CollapsibleTrigger asChild>
                      <button
                        type="button"
                        className="hover:bg-sidebar-accent mr-1 inline-flex shrink-0 items-center justify-center rounded-sm p-0.5 transition-colors"
                        aria-label={`Toggle ${workspace.name}`}
                      >
                        <ChevronRight
                          className={`size-3 transition-transform ${!hydrated || !collapsedSections.has(workspace.id) ? "rotate-90" : ""}`}
                        />
                      </button>
                    </CollapsibleTrigger>
                    <EmojiPicker
                      currentEmoji={workspace.icon}
                      onEmojiSelect={(emoji) => {
                        handleWorkspaceIcon(workspace.id, emoji);
                        setEmojiPickerWorkspaceId(null);
                      }}
                      onRemove={
                        workspace.icon
                          ? () => {
                              handleWorkspaceIcon(workspace.id, null);
                              setEmojiPickerWorkspaceId(null);
                            }
                          : undefined
                      }
                      open={emojiPickerWorkspaceId === workspace.id}
                      onOpenChange={(open) => setEmojiPickerWorkspaceId(open ? workspace.id : null)}
                    >
                      <button
                        type="button"
                        className="mr-1.5 inline-flex shrink-0 items-center justify-center transition-opacity hover:opacity-70"
                        title="Change icon"
                        aria-label={`Change ${workspace.name} icon`}
                      >
                        {workspace.icon ? (
                          <span className="text-xl leading-none">{workspace.icon}</span>
                        ) : (
                          <Building2 className="size-5" />
                        )}
                      </button>
                    </EmojiPicker>
                    <span className="truncate">{workspace.name}</span>
                  </div>
                </SidebarGroupLabel>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <SidebarGroupAction title={`Add to ${workspace.name}`}>
                      {addingTo === workspace.id ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Plus className="size-4" />
                      )}
                    </SidebarGroupAction>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent side="right" align="start">
                    <DropdownMenuItem onClick={() => handleAddRootPage(workspace.id)}>
                      <Plus className="mr-2 size-4" />
                      New page
                    </DropdownMenuItem>
                    {onImportToWorkspace && (
                      <DropdownMenuItem onClick={() => onImportToWorkspace(workspace.id)}>
                        <Upload className="mr-2 size-4" />
                        Import pages
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
                <CollapsibleContent>
                  <SidebarGroupContent className="pl-5">
                    <PageTree
                      pages={pagesByWorkspace.get(workspace.id) ?? []}
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
                      movingPageId={movingPageId}
                      starredIds={starredIds}
                      onToggleStar={onToggleStar}
                      onImport={onImport}
                      onDuplicate={onDuplicate}
                      onMoveToWorkspace={onMoveToWorkspace}
                    />
                  </SidebarGroupContent>
                </CollapsibleContent>
              </SidebarGroup>
            </Collapsible>
          </SortableWorkspaceItem>
        ))}
      </SortableContext>
    </DndContext>
  );
}
