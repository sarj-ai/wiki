"use client";

import { Copy } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { documentShortLink } from "@/lib/document-short-link";
import type { DocumentTransformResult } from "@/lib/document-transform-types";

const VISIBLE_FINDINGS = 3;

export function DocumentPolishReceipt({ result }: { result: DocumentTransformResult }) {
  const informationalFindings = uniqueFindingDetails(result, "informational");
  const reviewFindings = uniqueFindingDetails(result, "review");
  const alias = result.shortLinkStatus === "ready" ? result.shortLinkAlias : null;

  return (
    <div className="mt-3 space-y-3">
      {result.promotedHeadingCount > 0 ? (
        <p className="text-muted-foreground text-sm">
          Formatted {result.promotedHeadingCount} existing section{" "}
          {result.promotedHeadingCount === 1
            ? "label as a native heading"
            : "labels as native headings"}
          ; label wording is unchanged.
        </p>
      ) : null}
      <FindingList details={informationalFindings} label="Preserved safely" noun="coverage note" />
      <FindingList details={reviewFindings} label="Review notes" noun="review note" />
      {alias ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => {
              void navigator.clipboard
                .writeText(documentShortLink(window.location.origin, alias))
                .then(() => toast.success("Short link copied"))
                .catch(() => toast.error("Couldn’t copy short link"));
            }}
            type="button"
            variant="outline"
          >
            <Copy className="size-4" /> Copy short link
          </Button>
          <a
            className="rounded-sm text-sm underline focus-visible:outline-2"
            href={documentShortLink("", encodeURIComponent(alias))}
            rel="noopener noreferrer"
            target="_blank"
          >
            Open short link
          </a>
        </div>
      ) : (
        <p className="text-muted-foreground text-sm">
          {result.shortLinkStatus === "unavailable"
            ? "The short link could not be created."
            : "No automatic short link is available for this result."}{" "}
          Open the document and choose Short link to create one without polishing again.
        </p>
      )}
      <p className="text-muted-foreground text-xs">
        Short links do not change who can access the document.
      </p>
    </div>
  );
}

function uniqueFindingDetails(
  result: DocumentTransformResult,
  severity: "informational" | "review",
): string[] {
  return [
    ...new Set(
      result.findings
        .filter((finding) => (finding.severity ?? "review") === severity)
        .map((finding) => finding.detail.trim())
        .filter(Boolean),
    ),
  ];
}

function FindingList({ details, label, noun }: { details: string[]; label: string; noun: string }) {
  if (details.length === 0) return null;
  const remaining = details.slice(VISIBLE_FINDINGS);
  return (
    <div className="space-y-1 text-sm">
      <p className="font-medium">{label}</p>
      <ul className="list-disc space-y-1 pl-5">
        {details.slice(0, VISIBLE_FINDINGS).map((detail) => (
          <li key={detail}>{detail}</li>
        ))}
      </ul>
      {remaining.length > 0 ? (
        <details>
          <summary className="cursor-pointer rounded-sm underline focus-visible:outline-2">
            Show {remaining.length} more {noun}
            {remaining.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {remaining.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
