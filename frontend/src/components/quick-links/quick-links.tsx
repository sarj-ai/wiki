"use client";

import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { displayFileName, renderMimeIcon } from "@/lib/file-icons";
import { cn } from "@/lib/utils";

export interface QuickLinkItem {
  alias: string;
  driveName: null | string;
  lastVisitedAt: string;
  mimeType: string;
  name: string;
}

interface QuickLinksProps {
  aliases: QuickLinkItem[];
  error?: boolean;
  loading?: boolean;
}

const VISITED_AT_FORMAT = new Intl.DateTimeFormat("en", {
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  month: "short",
  timeZone: "UTC",
  timeZoneName: "short",
});

export function QuickLinks({ aliases, error = false, loading = false }: QuickLinksProps) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const linkRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filteredAliases = aliases.filter((item) => {
    if (normalizedQuery.length === 0) return true;
    return [item.name, item.alias, item.driveName ?? ""].some((value) =>
      value.toLocaleLowerCase().includes(normalizedQuery),
    );
  });
  const activeIndex = Math.min(selectedIndex, Math.max(filteredAliases.length - 1, 0));

  useEffect(() => {
    inputRef.current?.focus();
    const focusSearch = (event: KeyboardEvent) => {
      if (event.key !== "/" || isEditableTarget(event.target)) return;
      event.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", focusSearch);
    return () => {
      window.removeEventListener("keydown", focusSearch);
    };
  }, []);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (filteredAliases.length === 0 || event.nativeEvent.isComposing) return;
    switch (event.key) {
      case "ArrowDown": {
        event.preventDefault();
        setSelectedIndex((current) => (current + 1) % filteredAliases.length);
        break;
      }
      case "ArrowUp": {
        event.preventDefault();
        setSelectedIndex((current) => (current === 0 ? filteredAliases.length - 1 : current - 1));
        break;
      }
      case "Enter": {
        event.preventDefault();
        linkRefs.current[activeIndex]?.click();
        break;
      }
    }
  };

  return (
    <section className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-16 sm:px-8 md:py-24">
      <div className="relative">
        <Search
          aria-hidden
          className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
        />
        <Input
          aria-activedescendant={
            filteredAliases.length > 0 ? `quick-link-${activeIndex}` : undefined
          }
          aria-controls="quick-links-results"
          aria-expanded={filteredAliases.length > 0}
          aria-label="Search quick links"
          className="bg-card h-11 pl-9 text-base md:text-sm"
          disabled={error || loading || aliases.length === 0}
          onChange={(event) => {
            setQuery(event.target.value);
            setSelectedIndex(0);
          }}
          onKeyDown={handleKeyDown}
          placeholder="Search by page, short link, or drive…"
          ref={inputRef}
          role="combobox"
          value={query}
        />
      </div>

      <div
        aria-label="Recent quick links"
        className="bg-card mt-3 overflow-hidden rounded-xl border shadow-xs"
        id="quick-links-results"
        role="listbox"
      >
        {loading ? (
          <Message detail="Checking your current access…" title="Loading quick links" />
        ) : error ? (
          <Message
            detail="Your accessible quick links could not be loaded. Try refreshing the page."
            title="Unable to load quick links"
          />
        ) : aliases.length === 0 ? (
          <Message
            detail="Short links you open will appear here when you still have access to them."
            title="No recent quick links"
          />
        ) : filteredAliases.length === 0 ? (
          <Message
            detail="Try another page name, alias, or drive."
            title="No matching quick links"
          />
        ) : (
          filteredAliases.map((item, index) => (
            <a
              aria-selected={index === activeIndex}
              className={cn(
                "focus-visible:ring-ring flex min-h-16 items-center gap-3 border-b px-3 py-3 outline-none last:border-b-0 focus-visible:ring-2 focus-visible:ring-inset sm:px-4",
                index === activeIndex ? "bg-accent text-accent-foreground" : "hover:bg-accent/60",
              )}
              href={`/go/${encodeURIComponent(item.alias)}`}
              id={`quick-link-${index}`}
              key={item.alias}
              onFocus={() => {
                setSelectedIndex(index);
              }}
              ref={(element) => {
                linkRefs.current[index] = element;
              }}
              role="option"
            >
              <span className="bg-background flex size-9 shrink-0 items-center justify-center rounded-lg border">
                {renderMimeIcon(item.mimeType, "size-4")}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {displayFileName(item.name, item.mimeType)}
                </span>
                <span className="text-muted-foreground mt-0.5 flex min-w-0 items-center gap-1.5 text-xs">
                  <span className="truncate">{item.driveName ?? "My Drive"}</span>
                  <span aria-hidden>·</span>
                  <span className="truncate font-mono">go/{item.alias}</span>
                </span>
              </span>
              <time
                className="text-muted-foreground hidden shrink-0 text-xs sm:block"
                dateTime={item.lastVisitedAt}
              >
                {VISITED_AT_FORMAT.format(new Date(item.lastVisitedAt))}
              </time>
            </a>
          ))
        )}
      </div>

      {!error && !loading && aliases.length > 0 ? (
        <p className="text-muted-foreground mt-3 hidden text-xs sm:block">
          Use ↑ and ↓ to navigate, then Enter to open.
        </p>
      ) : null}
    </section>
  );
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable ||
    target.tagName === "INPUT" ||
    target.tagName === "SELECT" ||
    target.tagName === "TEXTAREA"
  );
}

function Message({ detail, title }: { detail: string; title: string }) {
  return (
    <div className="px-5 py-12 text-center">
      <p className="text-foreground text-sm font-medium">{title}</p>
      <p className="text-muted-foreground mx-auto mt-1 max-w-sm text-sm">{detail}</p>
    </div>
  );
}
