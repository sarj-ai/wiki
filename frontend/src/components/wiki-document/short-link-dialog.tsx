"use client";

import { Link2 } from "lucide-react";
import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";

const ShortLinkEditor = dynamic(
  async () => {
    const { ShortLinkEditor: Editor } = await import("./short-link-editor");
    return Editor;
  },
  { ssr: false },
);

export function ShortLinkDialog({ fileId }: { fileId: string }) {
  return <ShortLinkDialogForFile fileId={fileId} key={fileId} />;
}

function ShortLinkDialogForFile({ fileId }: { fileId: string }) {
  const dialog = useDeferredDialog();
  return (
    <>
      <Button
        aria-label="Short link"
        onClick={() => dialog.onOpenChange(true)}
        size="xs"
        type="button"
        variant="outline"
      >
        <Link2 className="size-3.5" />
        <span className="hidden @min-[40rem]/toolbar:inline">Short link</span>
      </Button>
      {dialog.mounted ? (
        <ShortLinkEditor fileId={fileId} onOpenChange={dialog.onOpenChange} open={dialog.open} />
      ) : null}
    </>
  );
}
