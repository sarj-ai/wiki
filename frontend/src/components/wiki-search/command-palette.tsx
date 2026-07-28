"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { Command as CommandPrimitive } from "cmdk";
import { ExternalLink, Loader2, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Command, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { useCommandPaletteState } from "@/hooks/use-command-palette";
import { useDriveSearch, useDriveSearchSnippet } from "@/hooks/use-drive-search";
import { expandPath, requestReveal } from "@/hooks/use-expanded-folders";
import { wikiKeys } from "@/hooks/use-wiki-tree";
import { isGoogleWorkspaceType, renderNodeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { resolveShortcutTarget, SHORTCUT_INACCESSIBLE_TOOLTIP } from "@/lib/shortcut-resolver";
import { cn } from "@/lib/utils";
import { wikiApi } from "@/lib/wiki-api";
import type { DriveSearchResult, DriveTree } from "@/types/wiki";
import { MIME_FOLDER, MIME_FORM } from "@/types/wiki";

const RELATIVE_TIME_FORMAT = new Intl.RelativeTimeFormat("en", { numeric: "always" });

const RELATIVE_TIME_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31536000],
  ["month", 2592000],
  ["week", 604800],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
  ["second", 1],
];

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

function Highlighted({ term, text }: { text: string; term: null | string }) {
  if (!term) return text;
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
      <mark className="bg-primary/20 text-foreground rounded-sm px-0.5 py-0.5" key={`m-${idx}`}>
        {text.slice(idx, idx + needle.length)}
      </mark>,
    );
    cursor = idx + needle.length;
  }
  return parts;
}

function relativeTime(iso: null | string): string {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const deltaSeconds = (then.getTime() - Date.now()) / 1000;
  const abs = Math.abs(deltaSeconds);
  for (const [unit, secondsPerUnit] of RELATIVE_TIME_UNITS) {
    if (abs >= secondsPerUnit || unit === "second") {
      return RELATIVE_TIME_FORMAT.format(Math.round(deltaSeconds / secondsPerUnit), unit);
    }
  }
  return "";
}

function ResultRow({
  driveName,
  onPrefetch,
  result,
}: {
  result: DriveSearchResult;
  driveName: string | undefined;
  onPrefetch: () => void;
}) {
  const inaccessible = result.shortcutInaccessible;
  const tooltip = inaccessible ? SHORTCUT_INACCESSIBLE_TOOLTIP : undefined;
  return (
    <CommandItem
      className={cn(
        "flex w-full items-start gap-3 rounded-md px-3 py-2.5 text-left text-sm",
        inaccessible && "text-muted-foreground/60",
      )}
      onPointerEnter={onPrefetch}
      title={tooltip}
      value={result.id}
    >
      <span className={cn("mt-0.5 shrink-0", inaccessible && "opacity-50")}>
        {renderNodeIcon(result, "size-4")}
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-foreground font-medium break-words">{result.name}</div>
        {driveName ? (
          <div className="text-muted-foreground text-xs break-words">{driveName}</div>
        ) : null}
      </div>
      {result.modifiedTime ? (
        <span className="text-muted-foreground mt-0.5 shrink-0 text-xs">
          {relativeTime(result.modifiedTime)}
        </span>
      ) : null}
    </CommandItem>
  );
}

function PreviewPanel({
  driveName,
  onOpen,
  query,
  result,
}: {
  result: DriveSearchResult | null;
  query: string;
  driveName: string | undefined;
  onOpen: (result: DriveSearchResult) => void;
}) {
  const target = result ? resolveShortcutTarget(result) : null;
  const inaccessible = result?.shortcutInaccessible === true;
  const snippetQuery = useDriveSearchSnippet(
    target && !inaccessible ? target.targetId : null,
    query,
    target !== null && !inaccessible,
    target !== null && target.targetMimeType !== "" ? target.targetMimeType : undefined,
  );

  if (!result || !target) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center p-6 text-sm">
        Select a result to preview the match.
      </div>
    );
  }

  const isForm = target.targetMimeType === MIME_FORM;
  const snippet = snippetQuery.data;

  return (
    <div className="flex h-full min-h-0 flex-col p-5">
      <div className="flex items-start gap-3">
        <span className="mt-1 shrink-0">{renderNodeIcon(result, "size-6")}</span>
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold break-words">{result.name}</div>
          {driveName ? <div className="text-muted-foreground text-xs">{driveName}</div> : null}
        </div>
      </div>

      <div className="mt-4 min-h-0 flex-1 overflow-auto">
        {inaccessible ? (
          <div className="text-muted-foreground text-sm text-pretty">
            This is a shortcut, but you don&apos;t have access to its target. Contact your admin if
            you need access.
          </div>
        ) : null}
        {!inaccessible && snippetQuery.isLoading ? (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 className="size-4 animate-spin" />
            Loading match preview…
          </div>
        ) : null}
        {!inaccessible && !snippetQuery.isLoading && !snippet?.snippet && (
          <div className="text-muted-foreground text-sm">
            {isGoogleWorkspaceType(target.targetMimeType)
              ? "Drive matched this file but we couldn't pinpoint the exact phrase in its text. Open it to see the match in context."
              : "Inline text preview isn't available for this file type — open it to view the content."}
          </div>
        )}
        {!inaccessible && snippet?.snippet ? (
          <p className="text-foreground text-sm leading-relaxed">
            <Highlighted term={snippet.matchTerm ?? query} text={snippet.snippet} />
          </p>
        ) : null}
      </div>

      <div className="mt-4 flex justify-end">
        <Button
          disabled={inaccessible}
          onClick={() => {
            onOpen(result);
          }}
          size="sm"
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
  const { open, setOpen } = useCommandPaletteState();
  const router = useRouter();
  const qc = useQueryClient();

  const [query, setQuery] = useState("");

  const [selectedResultId, setSelectedResultId] = useState<null | string>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const searchQuery = useDriveSearch(query);
  const results = searchQuery.data ?? [];

  const selectedId =
    selectedResultId !== null && results.some((r) => r.id === selectedResultId)
      ? selectedResultId
      : (results[0]?.id ?? "");
  const selectedResult = results.find((r) => r.id === selectedId) ?? null;
  const hasQuery = query.trim().length >= 2;

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
    if (result.shortcutInaccessible) {
      closePalette();
      return;
    }

    const target = resolveShortcutTarget(result);
    if (target.targetMimeType === MIME_FORM) {
      window.open(
        openUrlFor(target.targetMimeType, target.targetId),
        "_blank",
        "noopener,noreferrer",
      );
    } else if (target.targetMimeType === MIME_FOLDER) {
      const driveId = result.driveId;
      if (driveId) {
        expandPath(driveId, ancestorChain(qc, driveId, result.id));

        requestAnimationFrame(() => {
          const el = document.querySelector<HTMLElement>(
            `[data-file-id="${CSS.escape(result.id)}"]`,
          );
          el?.scrollIntoView({ block: "nearest" });
        });
      }
    } else {
      requestReveal(target.targetId);
      router.push(`/d/${encodeURIComponent(target.targetId)}`);
    }
    closePalette();
  };

  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (selectedResult) openResult(selectedResult);
  };

  const prefetchResult = (result: DriveSearchResult) => {
    if (result.shortcutInaccessible) return;
    const target = resolveShortcutTarget(result);
    if (target.targetMimeType === MIME_FORM || target.targetMimeType === MIME_FOLDER) return;
    void qc.prefetchQuery({
      queryFn: async () => wikiApi.getFile(target.targetId),
      queryKey: wikiKeys.file(target.targetId),
    });
    const trimmed = query.trim();
    if (trimmed.length >= 2) {
      void qc.prefetchQuery({
        queryFn: async () =>
          wikiApi.driveSearchSnippet(
            target.targetId,
            trimmed,
            target.targetMimeType !== "" ? target.targetMimeType : undefined,
          ),
        queryKey: wikiKeys.driveSearchSnippet(target.targetId, trimmed),
      });
    }
  };

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogContent
        className="flex h-[min(640px,85dvh)] !max-w-5xl flex-col gap-0 overflow-hidden p-0 sm:!max-w-5xl"
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          requestAnimationFrame(() => inputRef.current?.focus());
        }}
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">Search Drive</DialogTitle>
        <Command
          className="h-auto min-h-0 flex-1 bg-transparent"
          disablePointerSelection
          onValueChange={setSelectedResultId}
          shouldFilter={false}
          value={selectedId}
        >
          <div className="border-border flex items-center gap-2.5 border-b px-5 py-4">
            <Search className="text-muted-foreground size-5 shrink-0" />

            <CommandPrimitive.Input
              aria-label="Search Drive"
              className="placeholder:text-muted-foreground/70 flex-1 bg-transparent text-base leading-none outline-none"
              onKeyDown={handleInputKeyDown}
              onValueChange={setQuery}
              placeholder="Search Drive…"
              ref={inputRef}
              value={query}
            />
            {query.length > 0 && (
              <Button
                aria-label="Clear search"
                className="text-muted-foreground hover:text-foreground size-7 shrink-0"
                onClick={() => {
                  setQuery("");
                }}
                size="icon-sm"
                variant="ghost"
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
                {hasQuery && searchQuery.isFetching && results.length === 0 ? (
                  <div className="text-muted-foreground flex items-center gap-2 px-3 py-4 text-xs">
                    <Loader2 className="size-3 animate-spin" /> Searching…
                  </div>
                ) : null}
                {hasQuery && !searchQuery.isFetching && results.length === 0 ? (
                  <div className="text-muted-foreground px-3 py-4 text-xs">No results</div>
                ) : null}
                {results.map((result) => (
                  <ResultRow
                    driveName={result.driveName}
                    key={result.id}
                    onPrefetch={() => {
                      prefetchResult(result);
                    }}
                    result={result}
                  />
                ))}
              </div>
            </CommandList>

            <div className="min-w-0 flex-1">
              <PreviewPanel
                driveName={selectedResult?.driveName}
                onOpen={openResult}
                query={query.trim()}
                result={selectedResult}
              />
            </div>
          </div>
        </Command>

        <div className="border-border text-muted-foreground flex items-center justify-between gap-2 border-t px-5 py-2.5 text-[11px]">
          <span>
            <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">↑</kbd>{" "}
            <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">↓</kbd>{" "}
            navigate
            <span className="mx-2">·</span>
            <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">
              Enter
            </kbd>{" "}
            open
            <span className="mx-2">·</span>
            <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">
              Esc
            </kbd>{" "}
            close
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
