"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { FileText, Lock, Search, Users } from "lucide-react";
import { api } from "@/lib/api";
import { useWorkspaces } from "@/hooks/use-workspaces";

interface SearchResult {
  id: string;
  title: string;
  icon: string | null;
  workspaceId: string | null;
  isPrivate: boolean;
  parentId: string | null;
  googleDriveId: string;
  snippet: string | null;
  rank: number;
}

function HighlightMatch({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>;

  const words = query.trim().toLowerCase().split(/\s+/);
  const regex = new RegExp(`(${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  const parts = text.split(regex);

  return (
    <>
      {parts.map((part, i) =>
        words.some(w => part.toLowerCase() === w) ? (
          <mark key={i} className="cmd-highlight">{part}</mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const router = useRouter();
  const { data: workspaces = [] } = useWorkspaces();
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();
  const lastSearchRef = useRef(0);
  const panelRef = useRef<HTMLDivElement>(null);

  // Cmd+K / Ctrl+K to toggle
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
      if (e.key === "Escape" && open) {
        setOpen(false);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open]);

  // Reset on close
  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults([]);
    }
  }, [open]);

  // Debounced search
  const doSearch = useCallback(
    (value: string) => {
      setQuery(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);

      if (!value.trim()) {
        setResults([]);
        setIsSearching(false);
        return;
      }

      const now = Date.now();
      const timeSinceLast = now - lastSearchRef.current;
      // Fire immediately if it's been >300ms since last search, otherwise debounce
      const delay = timeSinceLast > 300 ? 0 : 100;

      setIsSearching(true);
      debounceRef.current = setTimeout(async () => {
        lastSearchRef.current = Date.now();
        try {
          const res = await api.search(value.trim());
          setResults(res);
        } catch {
          setResults([]);
        } finally {
          setIsSearching(false);
        }
      }, delay);
    },
    [],
  );

  function handleSelect(id: string) {
    setOpen(false);
    router.push(`/docs/${id}`);
  }

  function handleBackdropClick(e: React.MouseEvent) {
    // Only close if clicking the backdrop itself, not the panel
    if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
      setOpen(false);
    }
  }

  function getWorkspaceName(workspaceId: string | null): string | null {
    if (!workspaceId) return null;
    return workspaces.find((w) => w.id === workspaceId)?.name ?? null;
  }

  return (
    <>
      {/* Trigger button for sidebar */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="search-trigger"
      >
        <Search className="size-4 text-muted-foreground" />
        <span className="text-sm text-muted-foreground">Search...</span>
        <kbd className="search-trigger-kbd">
          <span className="text-xs">&#8984;</span>K
        </kbd>
      </button>

      {/* Portal the overlay + panel to body */}
      {open && typeof document !== "undefined" && createPortal(
        <div className="cmd-overlay" onClick={handleBackdropClick}>
          <div ref={panelRef} className="cmd-dialog">
            <Command shouldFilter={false} label="Search documents">
              <div className="cmd-input-wrapper">
                <Search className="size-4 text-muted-foreground shrink-0" />
                <Command.Input
                  value={query}
                  onValueChange={doSearch}
                  placeholder="Search documents..."
                  className="cmd-input"
                  autoFocus
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => doSearch("")}
                    className="text-xs text-muted-foreground hover:text-foreground px-1.5 py-0.5 rounded bg-muted"
                  >
                    Clear
                  </button>
                )}
              </div>

              <Command.List className="cmd-list">
                {isSearching && (
                  <Command.Loading className="cmd-loading">
                    <div className="cmd-loading-dots">
                      <span />
                      <span />
                      <span />
                    </div>
                  </Command.Loading>
                )}

                {!isSearching && query.trim() && results.length === 0 && (
                  <Command.Empty className="cmd-empty">
                    No documents found for &ldquo;{query}&rdquo;
                  </Command.Empty>
                )}

                {results.length > 0 && (
                  <Command.Group>
                    {results.map((result) => {
                      const wsName = getWorkspaceName(result.workspaceId);
                      return (
                        <Command.Item
                          key={result.id}
                          value={result.id}
                          onSelect={() => handleSelect(result.id)}
                          className="cmd-item"
                        >
                          <span className="cmd-item-icon">
                            {result.icon || <FileText className="size-4 text-muted-foreground" />}
                          </span>
                          <div className="cmd-item-content">
                            <span className="cmd-item-title">
                              <HighlightMatch text={result.title} query={query} />
                            </span>
                            {result.snippet && (
                              <span className="cmd-item-snippet">
                                <HighlightMatch text={result.snippet} query={query} />
                              </span>
                            )}
                            <span className="cmd-item-meta">
                              {result.isPrivate ? (
                                <>
                                  <Lock className="size-3" />
                                  <span>Private</span>
                                </>
                              ) : wsName ? (
                                <>
                                  <Users className="size-3" />
                                  <span>{wsName}</span>
                                </>
                              ) : null}
                            </span>
                          </div>
                        </Command.Item>
                      );
                    })}
                  </Command.Group>
                )}

                {!query.trim() && (
                  <div className="cmd-hint">
                    <p className="text-sm text-muted-foreground">
                      Type to search across all your documents
                    </p>
                  </div>
                )}
              </Command.List>
            </Command>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
