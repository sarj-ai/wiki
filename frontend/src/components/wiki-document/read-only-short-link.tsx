"use client";

import { Check, Copy, Link2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useDocumentAlias } from "@/hooks/use-document-alias";
import {
  documentShortLink,
  SHORT_LINK_COPY_FEEDBACK_MS,
  SHORT_LINK_DISPLAY_PREFIX,
} from "@/lib/document-short-link";

export function ReadOnlyShortLink({ fileId }: ReadOnlyShortLinkProps) {
  const aliasQuery = useDocumentAlias(fileId, true);
  const [copied, setCopied] = useState(false);
  const alias = aliasQuery.data?.alias;

  if (!alias) return null;

  const handleCopy = (): void => {
    void (async () => {
      await navigator.clipboard.writeText(documentShortLink(window.location.origin, alias));
      setCopied(true);
      window.setTimeout(() => {
        setCopied(false);
      }, SHORT_LINK_COPY_FEEDBACK_MS);
    })().catch(() => {
      toast.error("Couldn't copy short link");
    });
  };

  return (
    <Button
      aria-label={copied ? "Short link copied" : "Copy short link"}
      onClick={handleCopy}
      size="xs"
      title={`${SHORT_LINK_DISPLAY_PREFIX}${alias}`}
      type="button"
      variant="outline"
    >
      {copied ? <Check className="size-3.5" /> : <Link2 className="size-3.5" />}
      <span className="hidden sm:inline">{copied ? "Copied" : `go/${alias}`}</span>
      {!copied ? <Copy className="hidden size-3.5 sm:block" /> : null}
    </Button>
  );
}

interface ReadOnlyShortLinkProps {
  fileId: string;
}
