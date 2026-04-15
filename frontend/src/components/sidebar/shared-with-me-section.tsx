"use client";

import Link from "next/link";
import type { Page } from "@/types";
import { Share2, ChevronRight, FileText, FolderClosed } from "lucide-react";
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
} from "@/components/ui/sidebar";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

interface SharedWithMeSectionProps {
  pages: Page[];
  activePageId: string | null;
  collapsed: boolean;
  onToggle: () => void;
}

export function SharedWithMeSection({
  pages,
  activePageId,
  collapsed,
  onToggle,
}: SharedWithMeSectionProps) {
  if (pages.length === 0) return null;

  return (
    <Collapsible open={!collapsed} onOpenChange={onToggle}>
      <SidebarGroup>
        <SidebarGroupLabel asChild className="text-sidebar-foreground text-sm font-semibold">
          <div className="flex items-center">
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="hover:bg-sidebar-accent mr-1 inline-flex shrink-0 items-center justify-center rounded-sm p-0.5 transition-colors"
                aria-label="Toggle shared with me"
              >
                <ChevronRight
                  className={`size-3 transition-transform ${!collapsed ? "rotate-90" : ""}`}
                />
              </button>
            </CollapsibleTrigger>
            <Share2 className="mr-1 size-3.5" />
            <span className="truncate">Shared with me</span>
          </div>
        </SidebarGroupLabel>
        <CollapsibleContent>
          <SidebarGroupContent className="pl-5">
            <SidebarMenu>
              {pages.map((page) => (
                <SidebarMenuItem key={page.id}>
                  <SidebarMenuButton asChild isActive={activePageId === page.id} className="h-7">
                    <Link href={`/docs/${encodeURIComponent(page.id)}`}>
                      <span className="shrink-0 text-base leading-none">
                        {page.icon ||
                          (page.type === "section" ? (
                            <FolderClosed className="size-4" />
                          ) : (
                            <FileText className="size-4" />
                          ))}
                      </span>
                      <span className="truncate text-sm">{page.title}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}
