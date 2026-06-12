"use client";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Command as CommandPrimitive } from "cmdk";
import { formatDistanceToNowStrict } from "date-fns";
import { ExternalLink, Loader2, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import type { DriveSearchResult, DriveTree } from "@/types/wiki";

import { Button } from "@/components/ui/button";
import { Command, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useCommandPaletteHotkey, useCommandPaletteState } from "@/hooks/use-command-palette";
import { useDriveSearch, useDriveSearchSnippet } from "@/hooks/use-drive-search";
import { expandPath, requestReveal } from "@/hooks/use-expanded-folders";
import { wikiKeys } from "@/hooks/use-wiki-tree";
import { isGoogleWorkspaceType, renderNodeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { cn } from "@/lib/utils";
import { MIME_FOLDER, MIME_FORM, MIME_SHORTCUT } from "@/types/wiki";

// A search hit's "open" target is its shortcut target when present —
// otherwise the hit itself. Centralised so the row, the preview, and
// the open handler all agree on which mime/id pair we route through.
function resolveOpenTarget(result: DriveSearchResult): { mimeType: string; id: string } {
  if (result.mimeType === MIME_SHORTCUT) {
    return {
      mimeType: result.shortcutTargetMimeType ?? "",
      id: result.shortcutTargetId ?? result.id,
    };
  }
  return { mimeType: result.mimeType, id: result.id };
}

/**
 * Walk up the cached drive tree from ``nodeId`` to compute every
 * ancestor folder id along the path. Used by folder hits in the
 * command palette to expand the sidebar imperatively (the URL-driven
 * reveal effect doesn't fire for folders, since we no longer
 * navigate). Returns an empty list if the drive tree isn't cached or
 * the node isn't found — callers should still expand the drive root.
 */
function ancestorChain(qc: QueryClient, driveId: string, nodeId: string): string[] {
  const tree = qc.getQueryData<DriveTree>(wikiKeys.driveTree(driveId));
  if (!tree) return [];
  const byId = new Map(tree.folders.map((f) => [f.id, f]));
  const chain: string[] = [];
  let current = byId.get(nodeId);
  while (current?.parentId) {
    chain.unshift(current.parentId);
    current = byId.get(current.parentId);
  }
  return chain;
}

/**
 * Renders ``text`` with every case-insensitive occurrence of ``term``
 * wrapped in ``<mark>`` so the match stands out inside the preview.
 * Returns the input unchanged when ``term`` is empty.
 */
function Highlighted({ text, term }: { text: string; term: string | null }) {
  if (!term) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  const lower = text.toLowerCase();
  const needle = term.toLowerCase();
  let cursor = 0;
  while (cursor < text.length) {
    const idx = lower.indexOf(needle, cursor);
    if (idx < 0) {
      parts.push(text.slice(cursor));
      break;
    }
    if (idx > cursor) parts.push(text.slice(cursor, idx));
    parts.push(
      <mark key={`m-${idx}`} className="bg-primary/20 text-foreground rounded-sm px-0.5 py-0.5">
        {text.slice(idx, idx + needle.length)}
      </mark>,
    );
    cursor = idx + needle.length;
  }
  return <>{parts}</>;
}

function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  return formatDistanceToNowStrict(then, { addSuffix: true });
}

function ResultRow({ result, driveName }: { result: DriveSearchResult; driveName: string | null }) {
  const inaccessible = result.shortcutInaccessible === true;
  const tooltip = inaccessible
    ? "You don't have access to this shortcut's target. Contact your admin."
    : undefined;
  return (
    // Click/arrow only moves selection — preview on the right; hover
    // never preselects (the snippet fetch would race). Enter or the
    // preview's Open button navigates. Inaccessible hits stay enabled
    // (cmdk-disabled rows are skipped by keyboard nav) so the preview
    // can explain why; the open path is gated in openResult.
    <CommandItem
      value={result.id}
      title={tooltip}
      className={cn(
        "flex w-full items-start gap-3 rounded-md px-3 py-2.5 text-left text-sm",
        inaccessible && "text-muted-foreground/60",
      )}
    >
      <span className={cn("mt-0.5 shrink-0", inaccessible && "opacity-50")}>
        {renderNodeIcon(result, "size-4")}
      </span>
      <div className="min-w-0 flex-1">
        {/*
          Names can be very long (Drive doesn't enforce a length
          cap and slug-style filenames are common). break-words
          plus the parent's flex layout lets them wrap to multiple
          lines instead of truncating, so users can read the whole
          filename without hovering for a tooltip.
        */}
        <div className="text-foreground font-medium break-words">{result.name}</div>
        {driveName && <div className="text-muted-foreground text-xs break-words">{driveName}</div>}
      </div>
      {result.modifiedTime && (
        <span className="text-muted-foreground mt-0.5 shrink-0 text-xs">
          {relativeTime(result.modifiedTime)}
        </span>
      )}
    </CommandItem>
  );
}

function PreviewPanel({
  result,
  query,
  driveName,
  onOpen,
}: {
  result: DriveSearchResult | null;
  query: string;
  driveName: string | null;
  onOpen: (result: DriveSearchResult) => void;
}) {
  // Snippet fetch goes against the *target* — for shortcut hits the
  // shortcut entry has no exportable content. Inaccessible targets
  // skip the fetch entirely (it'd 403 against Drive's export).
  const target = result ? resolveOpenTarget(result) : null;
  const inaccessible = result?.shortcutInaccessible === true;
  const snippetQuery = useDriveSearchSnippet(
    target && !inaccessible ? target.id : null,
    query,
    target !== null && !inaccessible,
    target !== null && target.mimeType !== "" ? target.mimeType : undefined,
  );

  if (!result || !target) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center p-6 text-sm">
        Select a result to preview the match.
      </div>
    );
  }

  const isForm = target.mimeType === MIME_FORM;
  const snippet = snippetQuery.data;

  return (
    <div className="flex h-full min-h-0 flex-col p-5">
      <div className="flex items-start gap-3">
        <span className="mt-1 shrink-0">{renderNodeIcon(result, "size-6")}</span>
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold break-words">{result.name}</div>
          {driveName && <div className="text-muted-foreground text-xs">{driveName}</div>}
        </div>
      </div>

      <div className="mt-4 min-h-0 flex-1 overflow-auto">
        {inaccessible && (
          <div className="text-muted-foreground text-sm">
            This is a shortcut, but you don&apos;t have access to its target. Contact your admin if
            you need access.
          </div>
        )}
        {!inaccessible && snippetQuery.isLoading && (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            Loading match preview…
          </div>
        )}
        {!inaccessible && !snippetQuery.isLoading && !snippet?.snippet && (
          <div className="text-muted-foreground text-sm">
            {isGoogleWorkspaceType(target.mimeType)
              ? "Drive matched this file but we couldn't pinpoint the exact phrase in its text. Open it to see the match in context."
              : "Inline text preview isn't available for this file type — open it to view the content."}
          </div>
        )}
        {!inaccessible && snippet?.snippet && (
          <p className="text-foreground text-sm leading-relaxed">
            <Highlighted text={snippet.snippet} term={snippet.matchTerm ?? query} />
          </p>
        )}
      </div>

      <div className="mt-4 flex justify-end">
        <Button
          size="sm"
          disabled={inaccessible}
          onClick={() => {
            onOpen(result);
          }}
        >
          {isForm ? (
            <>
              <ExternalLink className="size-4" />
              Open in new tab
            </>
          ) : (
            "Open"
          )}
        </Button>
      </div>
    </div>
  );
}

export function CommandPalette() {
  useCommandPaletteHotkey();
  const { open, setOpen } = useCommandPaletteState();
  const router = useRouter();
  const qc = useQueryClient();

  const [query, setQuery] = useState("");
  // Selection is controlled by id, derived during render — id-stable
  // across debounced result swaps, no reset-from-effect.
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const searchQuery = useDriveSearch(query);
  const results = searchQuery.data ?? [];

  const selectedId =
    selectedResultId !== null && results.some((r) => r.id === selectedResultId)
      ? selectedResultId
      : (results[0]?.id ?? "");
  const selectedResult = results.find((r) => r.id === selectedId) ?? null;
  const hasQuery = query.trim().length >= 2;

  // Centralised close so programmatic dismissals (e.g. after openResult)
  // run the same reset as user-driven dismissals (Esc, click outside).
  // Radix's onOpenChange only fires for the latter, so calling
  // ``setOpen(false)`` directly used to leave the previous query in
  // state — surfacing as stale text on the next open.
  const closePalette = () => {
    setQuery("");
    setSelectedResultId(null);
    setOpen(false);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      closePalette();
      return;
    }
    setOpen(true);
  };

  const openResult = (result: DriveSearchResult) => {
    // Inaccessible shortcut hits have no working open path — Drive
    // would 403 on the target. Preview-panel button is disabled and
    // Enter is gated below; this guard covers any other call site.
    if (result.shortcutInaccessible) {
      closePalette();
      return;
    }
    // Shortcut hits route to the *target* — that's the file Drive
    // actually opens, the breadcrumb we want, and the id the sidebar
    // should reveal in its home drive's tree.
    const target = resolveOpenTarget(result);
    if (target.mimeType === MIME_FORM) {
      window.open(openUrlFor(target.mimeType, target.id), "_blank", "noopener,noreferrer");
    } else if (target.mimeType === MIME_FOLDER) {
      // Folders have no iframe content — ``/d/<folderId>`` would land
      // on Drive's "no preview" page. Expand the sidebar imperatively
      // instead: walk the cached drive tree from the result's id up to
      // its drive root, then expand each ancestor so the row becomes
      // visible. We use ``result.id`` (the shortcut entry, in the host
      // drive) — not ``target.id`` (the real folder, possibly in a
      // drive we never walked). For folder *shortcuts* this also
      // sidesteps inaccessible-target concerns: the reveal happens
      // entirely within the host drive.
      const driveId = result.driveId;
      if (driveId) {
        expandPath(driveId, ancestorChain(qc, driveId, result.id));
        // Best-effort scroll into view after the sidebar re-renders.
        // Folder rows don't carry data-file-id (only file rows do), so
        // we look up by row id via a generic data attribute. Falls
        // through gracefully if the row isn't yet in the DOM.
        requestAnimationFrame(() => {
          const el = document.querySelector<HTMLElement>(
            `[data-file-id="${CSS.escape(result.id)}"]`,
          );
          el?.scrollIntoView({ block: "nearest" });
        });
      }
    } else {
      // Tell the sidebar to walk its tree open to this file once the
      // breadcrumb arrives. Manual sidebar nav and back/forward never
      // set this, so they leave the user's collapse state alone.
      requestReveal(target.id);
      router.push(`/d/${encodeURIComponent(target.id)}`);
    }
    closePalette();
  };

  // Intercept Enter before cmdk's select-as-open (its handler bails on
  // defaultPrevented); isComposing guards IME confirmation keystrokes.
  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (selectedResult) openResult(selectedResult);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="flex h-[min(640px,85dvh)] !max-w-5xl flex-col gap-0 overflow-hidden p-0 sm:!max-w-5xl"
        // Hide shadcn's default floating close X — it lands right on
        // top of the search input at the top-right corner of our
        // palette. The footer strip documents Esc as the close key.
        showCloseButton={false}
        // Focus the input on open so typing immediately drives the
        // search. Radix's default auto-focus would hit the first
        // focusable child, which ruins the Spotlight UX.
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          requestAnimationFrame(() => inputRef.current?.focus());
        }}
      >
        {/*
          Radix requires a DialogTitle for screen-reader users. The
          palette's visual entry point is the search input, so the
          title is hidden visually while still being announced.
        */}
        <DialogTitle className="sr-only">Search Drive</DialogTitle>
        <Command
          shouldFilter={false}
          disablePointerSelection
          value={selectedId}
          onValueChange={setSelectedResultId}
          // h-auto overrides the wrapper's h-full so the footer keeps its slot.
          className="h-auto min-h-0 flex-1 bg-transparent"
        >
          <div className="border-border flex items-center gap-2.5 border-b px-5 py-4">
            <Search className="text-muted-foreground size-5 shrink-0" />
            {/* Raw cmdk Input — the shadcn wrapper hardcodes its own
                icon/chrome and can't host this flush header. */}
            <CommandPrimitive.Input
              ref={inputRef}
              value={query}
              onValueChange={setQuery}
              onKeyDown={handleInputKeyDown}
              placeholder="Search Drive…"
              className="placeholder:text-muted-foreground/70 flex-1 bg-transparent text-base leading-none outline-none"
              aria-label="Search Drive"
            />
            {query.length > 0 && (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => {
                  setQuery("");
                }}
                className="text-muted-foreground hover:text-foreground size-7 shrink-0"
                aria-label="Clear search"
              >
                <X className="size-4" />
              </Button>
            )}
          </div>

          <div className="flex min-h-0 flex-1">
            <CommandList className="h-full max-h-full w-2/5 min-w-0 border-r">
              <div className="space-y-0.5 p-3">
                {!hasQuery && (
                  <div className="text-muted-foreground px-3 py-4 text-xs">
                    Type at least 2 characters to search. Press ↑↓ to navigate, ⏎ to open.
                  </div>
                )}
                {hasQuery && searchQuery.isFetching && results.length === 0 && (
                  <div className="text-muted-foreground flex items-center gap-2 px-3 py-4 text-xs">
                    <Loader2 className="size-3 animate-spin" /> Searching…
                  </div>
                )}
                {hasQuery && !searchQuery.isFetching && results.length === 0 && (
                  <div className="text-muted-foreground px-3 py-4 text-xs">No results</div>
                )}
                {results.map((result) => (
                  <ResultRow key={result.id} result={result} driveName={result.driveName} />
                ))}
              </div>
            </CommandList>

            <div className="min-w-0 flex-1">
              <PreviewPanel
                result={selectedResult}
                query={query.trim()}
                driveName={selectedResult?.driveName ?? null}
                onOpen={openResult}
              />
            </div>
          </div>
        </Command>

        <div className="border-border text-muted-foreground flex items-center justify-between gap-2 border-t px-5 py-2.5 text-[11px]">
          <span>
            <kbd className="bg-muted rounded border px-1 py-0.5 font-mono text-[10px]">↑</kbd>{" "}
            <kbd className="bg-muted rounded border px-1 py-0.5 font-mono text-[10px]">↓</kbd>{" "}
            navigate
            <span className="mx-2">·</span>
            <kbd className="bg-muted rounded border px-1 py-0.5 font-mono text-[10px]">
              Enter
            </kbd>{" "}
            open
            <span className="mx-2">·</span>
            <kbd className="bg-muted rounded border px-1 py-0.5 font-mono text-[10px]">
              Esc
            </kbd>{" "}
            close
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
