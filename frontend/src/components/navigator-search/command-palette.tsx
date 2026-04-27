"use client";

import { formatDistanceToNowStrict } from "date-fns";
import { ExternalLink, Loader2, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import type { DriveSearchResult } from "@/types/navigator";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useCommandPaletteHotkey, useCommandPaletteState } from "@/hooks/use-command-palette";
import { useDriveSearch, useDriveSearchSnippet } from "@/hooks/use-drive-search";
import { requestReveal } from "@/hooks/use-expanded-folders";
import { isGoogleWorkspaceType, renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { cn } from "@/lib/utils";
import { MIME_FORM } from "@/types/navigator";

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

function ResultRow({
  result,
  driveName,
  selected,
  onSelect,
  rowRef,
}: {
  result: DriveSearchResult;
  driveName: string | null;
  selected: boolean;
  onSelect: () => void;
  rowRef: ((el: HTMLButtonElement | null) => void) | null;
}) {
  return (
    <button
      ref={rowRef ?? undefined}
      type="button"
      // Click (or arrow-key navigation) selects — preview appears on
      // the right. Hover does NOT preselect because the snippet fetch
      // would fire on every row a cursor passes through, racing the
      // intended selection. The explicit "Open" button in the preview
      // panel (or pressing Enter) is the only way to actually
      // navigate, matching how Spotlight lets you browse results.
      onClick={onSelect}
      className={cn(
        "hover:bg-accent/50 flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm outline-none",
        selected && "bg-accent",
      )}
    >
      <span className="shrink-0">{renderMimeIcon(result.mimeType, "size-4")}</span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{result.name}</div>
        {driveName && <div className="text-muted-foreground truncate text-xs">{driveName}</div>}
      </div>
      {result.modifiedTime && (
        <span className="text-muted-foreground shrink-0 text-xs">
          {relativeTime(result.modifiedTime)}
        </span>
      )}
    </button>
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
  const snippetQuery = useDriveSearchSnippet(result?.id ?? null, query, result !== null);

  if (!result) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center p-6 text-sm">
        Select a result to preview the match.
      </div>
    );
  }

  const isForm = result.mimeType === MIME_FORM;
  const snippet = snippetQuery.data;

  return (
    <div className="flex h-full min-h-0 flex-col p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 shrink-0">{renderMimeIcon(result.mimeType, "size-6")}</span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-semibold">{result.name}</div>
          {driveName && <div className="text-muted-foreground text-xs">{driveName}</div>}
        </div>
      </div>

      <div className="mt-4 min-h-0 flex-1 overflow-auto">
        {snippetQuery.isLoading && (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            Loading match preview…
          </div>
        )}
        {!snippetQuery.isLoading && !snippet?.snippet && (
          <div className="text-muted-foreground text-sm">
            {isGoogleWorkspaceType(result.mimeType)
              ? "Drive matched this file but we couldn't pinpoint the exact phrase in its text. Open it to see the match in context."
              : "Inline text preview isn't available for this file type — open it to view the content."}
          </div>
        )}
        {snippet?.snippet && (
          <p className="text-foreground text-sm leading-relaxed">
            <Highlighted text={snippet.snippet} term={snippet.matchTerm ?? query} />
          </p>
        )}
      </div>

      <div className="mt-4 flex justify-end">
        <Button size="sm" onClick={() => { onOpen(result); }}>
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

  const [query, setQuery] = useState("");
  // Store the highlighted result by its stable id, not by index —
  // otherwise the index would point at a stale position every time
  // the debounced search returns a new result set, forcing us to
  // reset state from an effect (which the React Compiler rule, and
  // React's own guidance, rightly flag as an anti-pattern).
  const [selectedResultId, setSelectedResultId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Row refs keyed by result id so the selection-changed effect can
  // scroll the currently-highlighted row into view when the user
  // arrow-keys past the edge of the visible list.
  const rowRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  const searchQuery = useDriveSearch(query);
  const results = searchQuery.data ?? [];

  const selectedIndex = (() => {
    if (!selectedResultId) return 0;
    const idx = results.findIndex((r) => r.id === selectedResultId);
    return idx >= 0 ? idx : 0;
  })();

  const handleOpenChange = (next: boolean) => {
    // Reset the palette state on close so the next open starts fresh.
    // Doing it here (event-driven) instead of in an effect keeps us
    // off the set-state-in-effect rule.
    if (!next) {
      setQuery("");
      setSelectedResultId(null);
    }
    setOpen(next);
  };

  const openResult = (result: DriveSearchResult) => {
    const isForm = result.mimeType === MIME_FORM;
    if (isForm) {
      window.open(openUrlFor(result.mimeType, result.id), "_blank", "noopener,noreferrer");
    } else {
      // Tell the sidebar to walk its tree open to this file once the
      // breadcrumb arrives. Manual sidebar nav and back/forward never
      // set this, so they leave the user's collapse state alone.
      requestReveal(result.id);
      router.push(`/d/${encodeURIComponent(result.id)}`);
    }
    setOpen(false);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (results.length === 0) return;
      const nextIdx = Math.min(selectedIndex + 1, results.length - 1);
      setSelectedResultId(results[nextIdx]?.id ?? null);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (results.length === 0) return;
      const nextIdx = Math.max(0, selectedIndex - 1);
      setSelectedResultId(results[nextIdx]?.id ?? null);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const result = results[selectedIndex];
      if (result) openResult(result);
    }
  };

  const selectedResult = results[selectedIndex] ?? null;
  const hasQuery = query.trim().length >= 2;

  // Sync the scroll position to the selected row — arrow-keying past
  // the bottom of the visible results should scroll the list. This
  // is a DOM-side-effect, not state manipulation, so it doesn't
  // violate the set-state-in-effect rule.
  useEffect(() => {
    if (!selectedResult) return;
    const row = rowRefs.current.get(selectedResult.id);
    row?.scrollIntoView({ block: "nearest" });
  }, [selectedResult]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="flex h-[min(560px,80dvh)] max-w-3xl flex-col gap-0 overflow-hidden p-0"
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
        <div className="border-border flex items-center gap-2 border-b px-4 py-3">
          <Search className="text-muted-foreground size-4 shrink-0" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); }}
            onKeyDown={handleKeyDown}
            placeholder="Search Drive…"
            className="h-8 border-0 bg-transparent p-0 text-base shadow-none focus-visible:ring-0"
            aria-label="Search Drive"
          />
          {query.length > 0 && (
            <button
              type="button"
              onClick={() => { setQuery(""); }}
              className="text-muted-foreground hover:text-foreground shrink-0"
              aria-label="Clear search"
            >
              <X className="size-4" />
            </button>
          )}
        </div>

        <div className="flex min-h-0 flex-1">
          <ScrollArea className="w-2/5 min-w-0 border-r">
            <div className="space-y-0.5 p-2">
              {!hasQuery && (
                <div className="text-muted-foreground px-2 py-4 text-xs">
                  Type at least 2 characters to search. Press ↑↓ to navigate, ⏎ to open.
                </div>
              )}
              {hasQuery && searchQuery.isFetching && results.length === 0 && (
                <div className="text-muted-foreground flex items-center gap-2 px-2 py-4 text-xs">
                  <Loader2 className="size-3 animate-spin" /> Searching…
                </div>
              )}
              {hasQuery && !searchQuery.isFetching && results.length === 0 && (
                <div className="text-muted-foreground px-2 py-4 text-xs">No results</div>
              )}
              {results.map((result, index) => (
                <ResultRow
                  key={result.id}
                  result={result}
                  driveName={result.driveName}
                  selected={index === selectedIndex}
                  onSelect={() => { setSelectedResultId(result.id); }}
                  rowRef={(el) => {
                    if (el) rowRefs.current.set(result.id, el);
                    else rowRefs.current.delete(result.id);
                  }}
                />
              ))}
            </div>
          </ScrollArea>

          <div className="min-w-0 flex-1">
            <PreviewPanel
              result={selectedResult}
              query={query.trim()}
              driveName={selectedResult?.driveName ?? null}
              onOpen={openResult}
            />
          </div>
        </div>

        <div className="border-border text-muted-foreground flex items-center justify-between gap-2 border-t px-4 py-2 text-[11px]">
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
