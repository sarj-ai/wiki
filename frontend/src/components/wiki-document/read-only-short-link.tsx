"use client";

import { Check, Copy, Link2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useDocumentAlias } from "@/hooks/use-document-alias";
import { documentShortLink, SHORT_LINK_DISPLAY_PREFIX } from "@/lib/document-short-link";

import { useCopyFeedback } from "./use-copy-feedback";

export function ReadOnlyShortLink({ fileId }: ReadOnlyShortLinkProps) {
  const aliasQuery = useDocumentAlias(fileId, true);
  const alias = aliasQuery.data?.alias;
  const { copied, showCopied } = useCopyFeedback(`${fileId}:${alias ?? ""}`);

  if (!alias) return null;

  const handleCopy = (): void => {
    void (async () => {
      await navigator.clipboard.writeText(documentShortLink(window.location.origin, alias));
      showCopied();
    })().catch(() => {
      toast.error("Couldn't copy short link");
    });
  };

  return (
    <Button
      aria-label={copied ? "Short link copied" : "Copy short link"}
      className="max-w-full"
      onClick={handleCopy}
      size="xs"
      title={`${SHORT_LINK_DISPLAY_PREFIX}${alias}`}
      type="button"
      variant="outline"
    >
      {copied ? <Check className="size-3.5" /> : <Link2 className="size-3.5" />}
      <span className="hidden min-w-0 truncate @min-[40rem]/toolbar:inline">
        {copied ? "Copied" : `go/${alias}`}
      </span>
      {!copied ? <Copy className="hidden size-3.5 @min-[40rem]/toolbar:block" /> : null}
    </Button>
  );
}

interface ReadOnlyShortLinkProps {
  fileId: string;
}
