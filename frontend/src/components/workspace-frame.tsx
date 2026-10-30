"use client";

import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";

export function WorkspaceFrame({ children }: { children: React.ReactNode }) {
  const { state } = useSidebar();
  return (
    <main className="flex h-svh min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <div
        className="bg-background flex h-12 shrink-0 items-center border-b px-2 md:data-[state=expanded]:hidden"
        data-state={state}
      >
        <SidebarTrigger size="icon-lg" />
        <span className="text-muted-foreground ml-2 text-sm">Navigation</span>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
    </main>
  );
}
