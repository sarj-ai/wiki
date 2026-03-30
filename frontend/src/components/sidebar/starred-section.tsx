"use client";

import { useMemo } from "react";
import Link from "next/link";
import type { Page } from "@/types";
import { Star, ChevronRight } from "lucide-react";
import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
} from "@/components/ui/sidebar";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

interface StarredSectionProps {
  starredIds: Set<string>;
  allPages: Page[];
  activePageId: string | null;
  collapsed: boolean;
  onToggle: () => void;
}

function buildPageMap(pages: Page[]): Map<string, Page> {
  const map = new Map<string, Page>();
  const stack = [...pages];
  while (stack.length > 0) {
    const page = stack.pop()!;
    map.set(page.id, page);
    for (const child of page.children) {
      stack.push(child);
    }
  }
  return map;
}

export function StarredSection({ starredIds, allPages, activePageId, collapsed, onToggle }: StarredSectionProps) {
  const pageMap = useMemo(() => buildPageMap(allPages), [allPages]);

  if (starredIds.size === 0) return null;

  const starredPages = [...starredIds]
    .map((id) => pageMap.get(id))
    .filter(Boolean) as Page[];

  if (starredPages.length === 0) return null;

  return (
    <Collapsible open={!collapsed} onOpenChange={onToggle}>
      <SidebarGroup>
        <SidebarGroupLabel asChild className="text-sm font-semibold text-sidebar-foreground">
          <div className="flex items-center">
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="mr-1 inline-flex shrink-0 items-center justify-center rounded-sm p-0.5 hover:bg-sidebar-accent transition-colors"
                aria-label="Toggle starred"
              >
                <ChevronRight className={`size-3 transition-transform ${!collapsed ? "rotate-90" : ""}`} />
              </button>
            </CollapsibleTrigger>
            <Star className="mr-1 size-3.5 fill-yellow-400 text-yellow-400" />
            <span className="truncate">Starred</span>
          </div>
        </SidebarGroupLabel>
        <CollapsibleContent>
          <SidebarGroupContent className="pl-5">
            <SidebarMenu>
              {starredPages.map((page) => (
                <SidebarMenuItem key={page.id}>
                  <SidebarMenuButton
                    asChild
                    isActive={activePageId === page.id}
                    className="h-7"
                  >
                    <Link href={`/docs/${encodeURIComponent(page.id)}`}>
                      <span className="shrink-0 text-base leading-none">
                        {page.icon ?? ""}
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
