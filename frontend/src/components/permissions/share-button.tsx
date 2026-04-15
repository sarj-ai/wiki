"use client";

import { useState } from "react";
import type { Page, Workspace } from "@/types";
import { Button } from "@/components/ui/button";
import { UserPlus } from "lucide-react";
import { ShareDialog } from "@/components/permissions/share-dialog";

interface ShareButtonProps {
  page: Page;
  workspace: Workspace | null;
}

export function ShareButton({ page, workspace }: ShareButtonProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="gap-2 hover:border-[#251040] hover:bg-[#251040] hover:text-white dark:hover:border-[#4B3483] dark:hover:bg-[#4B3483] dark:hover:text-white"
        onClick={() => setOpen(true)}
      >
        <UserPlus className="size-4" />
        Share
      </Button>
      <ShareDialog page={page} workspace={workspace} open={open} onOpenChange={setOpen} />
    </>
  );
}
