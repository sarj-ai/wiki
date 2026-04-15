"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { renderMimeIcon } from "@/lib/file-icons";
import { useDriveSearch } from "@/hooks/use-drive-search";

export function DriveSearchBox() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const { data: results = [], isFetching, isError } = useDriveSearch(query);

  const handleSelect = (id: string) => {
    router.push(`/d/${encodeURIComponent(id)}`);
    setOpen(false);
    setQuery("");
  };

  const hasQuery = query.trim().length >= 2;

  return (
    <Popover open={open && hasQuery} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="relative">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2" />
          <Input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => {
              if (hasQuery) setOpen(true);
            }}
            placeholder="Search Drive…"
            className="h-8 pr-7 pl-7 text-xs"
            aria-label="Search Drive"
          />
          {query.length > 0 && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setOpen(false);
              }}
              className="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 size-3.5 -translate-y-1/2"
              aria-label="Clear search"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        sideOffset={4}
        // Keep focus in the input while results are open so keystrokes
        // continue to refine the query instead of being swallowed by the
        // popover.
        onOpenAutoFocus={(e) => e.preventDefault()}
        className="max-h-80 w-(--radix-popover-trigger-width) overflow-auto p-1"
      >
        {isFetching && <div className="text-muted-foreground px-2 py-1.5 text-xs">Searching…</div>}
        {!isFetching && isError && (
          <div className="text-destructive px-2 py-1.5 text-xs">Search failed</div>
        )}
        {!isFetching && !isError && results.length === 0 && (
          <div className="text-muted-foreground px-2 py-1.5 text-xs">No results</div>
        )}
        {!isFetching &&
          !isError &&
          results.map((result) => (
            <button
              key={result.id}
              type="button"
              onClick={() => handleSelect(result.id)}
              className="hover:bg-accent focus:bg-accent flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm outline-none"
            >
              {renderMimeIcon(result.mimeType, "size-4 shrink-0")}
              <span className="truncate">{result.name}</span>
            </button>
          ))}
      </PopoverContent>
    </Popover>
  );
}
