"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { Command as CommandPrimitive } from "cmdk";
import { ExternalLink, Loader2, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useDebouncedCallback } from "use-debounce";

import { Button } from "@/components/ui/button";
import { Command, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { FocusReturnProvider } from "@/components/ui/focus-return";
import { useCommandPaletteState } from "@/hooks/use-command-palette";
import { useDriveSearchSnippet, useDriveSearchState } from "@/hooks/use-drive-search";
import { expandPath, requestReveal } from "@/hooks/use-expanded-folders";
import { fileQueryOptions, wikiKeys } from "@/hooks/use-wiki-tree";
import { ApiError, RATE_LIMIT_MESSAGE } from "@/lib/api-error";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { displayFileName, isGoogleWorkspaceType, renderNodeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { folderAncestorIds } from "@/lib/folder-ancestor-ids";
import { handlePrefetchError } from "@/lib/handle-prefetch-error";
import { resolveShortcutTarget, SHORTCUT_INACCESSIBLE_TOOLTIP } from "@/lib/shortcut-resolver";
import { allowsReusableSpeculation, readNavigatorHints } from "@/lib/speculation-policy";
import { cn } from "@/lib/utils";
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

export function CommandPalette() {
  const { onDocumentNavigate, open, returnFocusRef, setOpen } = useCommandPaletteState();
  const router = useRouter();
  const qc = useQueryClient();
  const userId = useBrowserUserId();

  const [query, setQuery] = useState("");

  const [selectedResultId, setSelectedResultId] = useState<null | string>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const focusFrame = useRef<null | number>(null);
  const focusOwner = useRef<null | object>(null);
  useLayoutEffect(() => {
    const owner = {};
    focusOwner.current = open ? owner : null;
    return () => {
      focusOwner.current = null;
      if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
      focusFrame.current = null;
    };
  }, [open, userId]);

  const { query: searchQuery, isCurrentQuery } = useDriveSearchState(query, open);
  const hasQuery = query.trim().length >= 2;
  const results = hasQuery && isCurrentQuery ? (searchQuery.data ?? []) : [];

  const selectedId =
    selectedResultId !== null && results.some((r) => r.id === selectedResultId)
      ? selectedResultId
      : (results[0]?.id ?? "");
  const selectedResult = results.find((r) => r.id === selectedId) ?? null;
  const selectedIsFolder =
    selectedResult !== null && resolveShortcutTarget(selectedResult).targetMimeType === MIME_FOLDER;
  const searching =
    hasQuery &&
    (!isCurrentQuery || searchQuery.isPending || (searchQuery.isFetching && results.length === 0));

  const prepareResult = (result: DriveSearchResult): void => {
    if (result.shortcutInaccessible) return;
    const target = resolveShortcutTarget(result);
    if (target.targetMimeType === MIME_FORM || target.targetMimeType === MIME_FOLDER) return;
    void qc.query(fileQueryOptions(target.targetId)).catch(handlePrefetchError);
    router.prefetch(`/d/${encodeURIComponent(target.targetId)}`);
  };
  const schedulePrepareResult = useDebouncedCallback((result: DriveSearchResult) => {
    if (
      document.visibilityState !== "visible" ||
      !allowsReusableSpeculation(readNavigatorHints(navigator))
    )
      return;
    prepareResult(result);
  }, 150);

  useEffect(() => {
    schedulePrepareResult.cancel();
    if (open && selectedResult) schedulePrepareResult(selectedResult);
    return schedulePrepareResult.cancel;
  }, [open, schedulePrepareResult, selectedResult]);

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

  const openResult = (result: DriveSearchResult, navigationEligible = true) => {
    schedulePrepareResult.cancel();
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
        expandPath(userId, driveId, ancestorChain(qc, driveId, result.id));

        requestAnimationFrame(() => {
          const el = document.querySelector<HTMLElement>(
            `[data-file-id="${CSS.escape(result.id)}"]`,
          );
          el?.scrollIntoView({ block: "nearest" });
        });
      }
    } else {
      prepareResult(result);
      requestReveal(target.targetId);
      const href = `/d/${encodeURIComponent(target.targetId)}` as const;
      if (navigationEligible) onDocumentNavigate?.(href);
      router.push(href);
    }
    closePalette();
  };

  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.defaultPrevented) return;
    event.preventDefault();
    if (selectedResult) openResult(selectedResult, isUnmodifiedActivation(event));
  };

  return (
    <FocusReturnProvider targetRef={returnFocusRef}>
      <Dialog onOpenChange={handleOpenChange} open={open}>
        <DialogContent
          className="flex h-[min(640px,85dvh)] !max-w-5xl flex-col gap-0 overflow-hidden p-0 sm:!max-w-5xl"
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            const owner = focusOwner.current;
            if (!owner) return;
            if (focusFrame.current !== null) cancelAnimationFrame(focusFrame.current);
            focusFrame.current = requestAnimationFrame(() => {
              focusFrame.current = null;
              if (focusOwner.current === owner) inputRef.current?.focus();
            });
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
            <div className="border-border flex shrink-0 items-center gap-2.5 border-b p-3 sm:px-5 sm:py-4">
              <Search className="text-muted-foreground size-5 shrink-0" />

              <CommandPrimitive.Input
                aria-label="Search Drive"
                className="placeholder:text-muted-foreground min-w-0 flex-1 bg-transparent text-base leading-none outline-none"
                maxLength={2048}
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
                    setSelectedResultId(null);
                    inputRef.current?.focus();
                  }}
                  size="icon-sm"
                  variant="ghost"
                >
                  <X className="size-4" />
                </Button>
              )}
            </div>

            <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
              <CommandList className="max-h-none min-h-0 w-full min-w-0 flex-1 border-b sm:h-full sm:max-h-full sm:w-2/5 sm:flex-none sm:border-r sm:border-b-0">
                <div className="space-y-0.5 p-3">
                  {!hasQuery && (
                    <div className="text-muted-foreground px-3 py-4 text-xs">
                      Type at least 2 characters to search. Press ↑↓ to navigate, ⏎ to open.
                    </div>
                  )}
                  {searching ? (
                    <div
                      className="text-muted-foreground flex items-center gap-2 px-3 py-4 text-xs"
                      role="status"
                    >
                      <Loader2 className="size-3 animate-spin" /> Searching…
                    </div>
                  ) : null}
                  {hasQuery && isCurrentQuery && searchQuery.isError ? (
                    <QueryError
                      error={searchQuery.error}
                      fallback="Search is temporarily unavailable."
                      label="Retry search"
                      onRetry={() => void searchQuery.refetch()}
                      retrying={searchQuery.isFetching}
                    />
                  ) : null}
                  {hasQuery &&
                  isCurrentQuery &&
                  searchQuery.isSuccess &&
                  !searchQuery.isFetching &&
                  results.length === 0 ? (
                    <div className="text-muted-foreground px-3 py-4 text-xs">No results</div>
                  ) : null}
                  {results.map((result) => (
                    <ResultRow driveName={result.driveName} key={result.id} result={result} />
                  ))}
                </div>
              </CommandList>

              <div className="min-h-0 min-w-0 flex-1">
                <PreviewPanel
                  driveName={selectedResult?.driveName}
                  onOpen={openResult}
                  query={query.trim()}
                  result={selectedResult}
                />
              </div>
            </div>
          </Command>

          <div className="border-border text-muted-foreground flex shrink-0 flex-wrap items-center justify-between gap-2 border-t px-3 py-2.5 text-[11px] sm:px-5">
            <span className="hidden sm:inline">
              <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">↑</kbd>{" "}
              <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">↓</kbd>{" "}
              navigate
              <span className="mx-2">·</span>
              <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">
                Enter
              </kbd>{" "}
              {selectedIsFolder ? "show in sidebar" : "open"}
              <span className="mx-2">·</span>
              <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-[10px]">
                Esc
              </kbd>{" "}
              close
            </span>
            <Button aria-label="Close search" onClick={closePalette} size="sm" variant="ghost">
              Close
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </FocusReturnProvider>
  );
}

function ancestorChain(qc: QueryClient, driveId: string, nodeId: string): string[] {
  const tree = qc.getQueryData<DriveTree>(wikiKeys.driveTree(driveId));
  return tree ? folderAncestorIds(tree.folders, nodeId) : [];
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
      <mark className="bg-primary/20 text-foreground rounded-sm p-0.5" key={`m-${idx}`}>
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
  result,
}: {
  result: DriveSearchResult;
  driveName: string | undefined;
}) {
  const inaccessible = result.shortcutInaccessible;
  const tooltip = inaccessible ? SHORTCUT_INACCESSIBLE_TOOLTIP : undefined;
  const displayName = displayFileName(
    result.name,
    result.shortcutTargetMimeType ?? result.mimeType,
  );
  return (
    <CommandItem
      className={cn(
        "flex w-full items-start gap-3 rounded-md px-3 py-2.5 text-left text-sm",
        inaccessible && "text-muted-foreground/60",
      )}
      title={tooltip}
      value={result.id}
    >
      <span className={cn("mt-0.5 shrink-0", inaccessible && "opacity-50")}>
        {renderNodeIcon(result, "size-4")}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
          <div className="text-foreground min-w-0 flex-1 font-medium wrap-anywhere">
            {displayName}
          </div>
          {result.modifiedTime ? (
            <span className="text-muted-foreground shrink-0 text-xs">
              {relativeTime(result.modifiedTime)}
            </span>
          ) : null}
        </div>
        {driveName ? (
          <div className="text-muted-foreground text-xs wrap-anywhere">{driveName}</div>
        ) : null}
      </div>
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
  onOpen: (result: DriveSearchResult, navigationEligible: boolean) => void;
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
  const isFolder = target.targetMimeType === MIME_FOLDER;
  const displayName = displayFileName(result.name, target.targetMimeType);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3 sm:space-y-4 sm:p-5">
        <div className="flex items-start gap-3">
          <span className="mt-1 shrink-0">{renderNodeIcon(result, "size-6")}</span>
          <div className="min-w-0 flex-1">
            <div className="text-base font-semibold break-words">{displayName}</div>
            {driveName ? <div className="text-muted-foreground text-xs">{driveName}</div> : null}
          </div>
        </div>

        <MatchPreview
          inaccessible={inaccessible}
          isFolder={isFolder}
          mimeType={target.targetMimeType}
          query={query}
          snippetQuery={snippetQuery}
        />
      </div>
      <div className="border-border flex shrink-0 justify-end border-t px-3 py-2 sm:px-5 sm:py-3">
        <Button
          disabled={inaccessible}
          onClick={(event) => {
            if (event.defaultPrevented) return;
            onOpen(result, isUnmodifiedActivation(event));
          }}
          size="sm"
        >
          {isForm ? (
            <>
              <ExternalLink className="size-4" />
              Open in new tab
            </>
          ) : isFolder ? (
            "Show in sidebar"
          ) : (
            "Open"
          )}
        </Button>
      </div>
    </div>
  );
}

function isUnmodifiedActivation(event: {
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}): boolean {
  return !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
}

function MatchPreview({
  inaccessible,
  isFolder,
  mimeType,
  query,
  snippetQuery,
}: {
  inaccessible: boolean;
  isFolder: boolean;
  mimeType: string;
  query: string;
  snippetQuery: ReturnType<typeof useDriveSearchSnippet>;
}) {
  if (inaccessible)
    return (
      <div className="text-muted-foreground text-sm text-pretty">
        This is a shortcut, but you don&apos;t have access to its target. Contact your admin if you
        need access.
      </div>
    );
  if (snippetQuery.isLoading)
    return (
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" /> Loading match preview…
      </div>
    );
  if (snippetQuery.isError)
    return (
      <QueryError
        error={snippetQuery.error}
        fallback="The match preview is temporarily unavailable."
        label="Retry preview"
        onRetry={() => void snippetQuery.refetch()}
        retrying={snippetQuery.isFetching}
      />
    );
  const snippet = snippetQuery.data;
  if (snippet?.snippet)
    return (
      <p className="text-foreground text-sm leading-relaxed">
        <Highlighted term={snippet.matchTerm ?? query} text={snippet.snippet} />
      </p>
    );
  const message = isFolder
    ? "Show this folder in the sidebar to browse its contents."
    : isGoogleWorkspaceType(mimeType)
      ? "Drive matched this file but we couldn't pinpoint the exact phrase in its text. Open it to see the match in context."
      : "Inline text preview isn't available for this file type — open it to view the content.";
  return <div className="text-muted-foreground text-sm">{message}</div>;
}

function QueryError({
  error,
  fallback,
  label,
  onRetry,
  retrying,
}: {
  error: unknown;
  fallback: string;
  label: string;
  onRetry: () => void;
  retrying: boolean;
}) {
  const message = error instanceof ApiError && error.status === 429 ? RATE_LIMIT_MESSAGE : fallback;
  return (
    <div
      className="text-muted-foreground flex flex-col items-start gap-2 px-3 py-4 text-xs"
      role="alert"
    >
      <span>{message}</span>
      <Button disabled={retrying} onClick={onRetry} size="xs" type="button" variant="outline">
        {label}
      </Button>
    </div>
  );
}
