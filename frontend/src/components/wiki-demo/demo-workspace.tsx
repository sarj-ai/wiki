"use client";

import { LockKeyhole } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import { WorkspaceFrame } from "@/components/workspace-frame";

import { type DemoFile, demoFileFor, demoHref } from "./demo-data";
import { DemoPreview } from "./demo-preview";
import { DemoSearch } from "./demo-search";
import { DemoSidebar } from "./demo-sidebar";
import { DemoFileContext, DemoToolbar } from "./demo-toolbar";

export function DemoWorkspace() {
  return (
    <SidebarProvider persistState={false}>
      <DemoWorkspaceContent />
    </SidebarProvider>
  );
}

function DemoWorkspaceContent() {
  const params = useSearchParams();
  const file = demoFileFor(params.get("file"));
  const router = useRouter();
  const { setOpenMobile } = useSidebar();
  const [searchOpen, setSearchOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    document.title = `${file.name} · Wiki demo`;
  }, [file.name]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const closeMobile = () => setOpenMobile(false);
  const selectFile = (selected: DemoFile) => {
    setSearchOpen(false);
    closeMobile();
    router.push(demoHref(selected));
  };
  const reload = () => setReloadKey((current) => current + 1);

  return (
    <>
      <a
        className="bg-background focus-visible:ring-ring fixed top-2 left-2 z-50 -translate-y-20 rounded-md border px-3 py-2 text-sm focus:translate-y-0 focus-visible:ring-2"
        href="#demo-document"
      >
        Skip to document
      </a>
      <DemoSidebar file={file} onNavigate={closeMobile} onSearch={() => setSearchOpen(true)} />
      <WorkspaceFrame>
        <div className="flex size-full min-h-0 flex-col" id="demo-document" tabIndex={-1}>
          <DemoToolbar file={file} key={file.id} onReload={reload} />
          <DemoFileContext file={file} />
          <DemoPreview file={file} key={`${file.id}:${reloadKey}`} onReload={reload} />
          <div className="bg-background text-muted-foreground flex h-7 shrink-0 items-center gap-1.5 border-t px-3 text-xs">
            <LockKeyhole aria-hidden className="size-3" />
            <span>Public demo · Read only</span>
            <span className="ml-auto hidden sm:inline">Fictional sample files</span>
          </div>
        </div>
      </WorkspaceFrame>
      <DemoSearch onOpenChange={setSearchOpen} onSelect={selectFile} open={searchOpen} />
    </>
  );
}
