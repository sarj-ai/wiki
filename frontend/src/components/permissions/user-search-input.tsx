"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import type { Permission, PagePermission, UserSearchResult, WorkspaceSearchResult } from "@/types";
import { useLoading } from "@/components/loading-bar";
import { api } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Loader2, Users } from "lucide-react";
import { getInitials } from "./utils";

type SearchSuggestion =
  | { kind: "user"; data: UserSearchResult }
  | { kind: "workspace"; data: WorkspaceSearchResult };

interface UserSearchInputProps {
  pageId: string;
  pageType: "page" | "section";
  permissions: PagePermission[];
  onSelectUser: (newPermission: PagePermission) => void;
}

export function UserSearchInput({
  pageId,
  pageType,
  permissions,
  onSelectUser,
}: UserSearchInputProps) {
  const { start, done } = useLoading();

  const [searchQuery, setSearchQuery] = useState("");
  const [invitePermission, setInvitePermission] = useState<Permission>("view");
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [grantingId, setGrantingId] = useState<string | null>(null);
  const searchRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const searchCounterRef = useRef(0);

  // Recursive share confirmation state
  const [pendingGrant, setPendingGrant] = useState<SearchSuggestion | null>(null);
  const [showRecursiveDialog, setShowRecursiveDialog] = useState(false);

  // Debounced search for users and workspaces
  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchQuery(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);

      if (value.trim().length < 2) {
        setSuggestions([]);
        setShowSuggestions(false);
        return;
      }

      debounceRef.current = setTimeout(async () => {
        const requestId = ++searchCounterRef.current;
        setIsSearching(true);
        try {
          const [users, workspaces] = await Promise.all([
            api.searchUsers(value.trim()),
            api.searchWorkspaces(value.trim()),
          ]);
          if (requestId !== searchCounterRef.current) return;

          const existingUserIds = new Set(permissions.filter((p) => p.userId).map((p) => p.userId));
          const existingWorkspaceIds = new Set(
            permissions.filter((p) => p.workspaceId).map((p) => p.workspaceId),
          );

          const merged: SearchSuggestion[] = [
            ...workspaces
              .filter((ws) => !existingWorkspaceIds.has(ws.id))
              .map((ws) => ({ kind: "workspace" as const, data: ws })),
            ...users
              .filter((u) => !existingUserIds.has(u.id))
              .map((u) => ({ kind: "user" as const, data: u })),
          ];

          setSuggestions(merged);
          setShowSuggestions(true);
        } catch {
          if (requestId === searchCounterRef.current) {
            setSuggestions([]);
          }
        } finally {
          if (requestId === searchCounterRef.current) {
            setIsSearching(false);
          }
        }
      }, 300);
    },
    [permissions],
  );

  // Close suggestions on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function executeGrant(suggestion: SearchSuggestion, recursive: boolean) {
    const id = suggestion.data.id;
    setGrantingId(id);
    start();
    try {
      const newPerm = await api.grantPermission({
        pageId,
        userId: suggestion.kind === "user" ? id : undefined,
        workspaceId: suggestion.kind === "workspace" ? id : undefined,
        permission: invitePermission,
        recursive,
      });
      onSelectUser(newPerm);
      setSearchQuery("");
      setSuggestions([]);
      setShowSuggestions(false);
    } catch {
      // Could show error toast
    } finally {
      setGrantingId(null);
      done();
    }
  }

  function handleSelect(suggestion: SearchSuggestion) {
    if (pageType === "section") {
      // Section page — ask about recursive sharing
      setPendingGrant(suggestion);
      setShowRecursiveDialog(true);
    } else {
      executeGrant(suggestion, false);
    }
  }

  function handleRecursiveConfirm(recursive: boolean) {
    setShowRecursiveDialog(false);
    if (pendingGrant) {
      executeGrant(pendingGrant, recursive);
      setPendingGrant(null);
    }
  }

  const pendingName = pendingGrant
    ? pendingGrant.kind === "workspace"
      ? pendingGrant.data.name
      : (pendingGrant.data.displayName ?? pendingGrant.data.email)
    : "";

  return (
    <>
      <div className="flex gap-2">
        <div className="relative flex-1" ref={searchRef}>
          <Input
            placeholder="Search people or groups..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            onFocus={() => {
              if (suggestions.length > 0) setShowSuggestions(true);
            }}
            className="h-9"
          />
          {showSuggestions && (
            <div className="bg-popover absolute top-full right-0 left-0 z-50 mt-1 max-h-[200px] overflow-y-auto rounded-md border p-1 shadow-md">
              {isSearching && (
                <div className="flex items-center justify-center py-3">
                  <Loader2 className="text-muted-foreground size-4 animate-spin" />
                </div>
              )}
              {!isSearching && suggestions.length === 0 && searchQuery.trim().length >= 2 && (
                <p className="text-muted-foreground px-2 py-2 text-sm">No results found</p>
              )}
              {suggestions.map((s) => {
                const id = s.data.id;
                const isGranting = grantingId === id;

                if (s.kind === "workspace") {
                  return (
                    <button
                      key={`ws-${id}`}
                      type="button"
                      disabled={isGranting}
                      onClick={() => handleSelect(s)}
                      className="hover:bg-accent flex w-full items-center gap-3 rounded-sm px-2 py-2 text-left transition-colors disabled:opacity-50"
                    >
                      <div className="bg-secondary flex size-7 items-center justify-center rounded-full">
                        <Users className="text-secondary-foreground size-3.5" />
                      </div>
                      <div className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-sm font-medium">{s.data.name}</span>
                        <span className="text-muted-foreground truncate text-xs">
                          {s.data.googleGroupEmail ?? "Group"}
                        </span>
                      </div>
                      {isGranting && (
                        <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />
                      )}
                    </button>
                  );
                }

                return (
                  <button
                    key={`u-${id}`}
                    type="button"
                    disabled={isGranting}
                    onClick={() => handleSelect(s)}
                    className="hover:bg-accent flex w-full items-center gap-3 rounded-sm px-2 py-2 text-left transition-colors disabled:opacity-50"
                  >
                    <Avatar className="size-7">
                      {s.data.avatarUrl && (
                        <AvatarImage src={s.data.avatarUrl} alt={s.data.displayName ?? ""} />
                      )}
                      <AvatarFallback className="text-xs">
                        {getInitials(s.data.displayName ?? s.data.email)}
                      </AvatarFallback>
                    </Avatar>
                    <div className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm font-medium">
                        {s.data.displayName ?? s.data.email}
                      </span>
                      <span className="text-muted-foreground truncate text-xs">{s.data.email}</span>
                    </div>
                    {isGranting && (
                      <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <Select
          value={invitePermission}
          onValueChange={(v) => setInvitePermission(v as Permission)}
        >
          <SelectTrigger className="h-9 w-[110px]" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="view">Can view</SelectItem>
            <SelectItem value="edit">Can edit</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Recursive share confirmation */}
      <AlertDialog open={showRecursiveDialog} onOpenChange={setShowRecursiveDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Share with sub-pages?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="text-muted-foreground space-y-3 text-sm">
                <p>
                  This section has existing sub-pages. How should{" "}
                  <span className="text-foreground font-medium">{pendingName}</span>
                  &apos;s access work?
                </p>
                <ul className="list-disc space-y-1.5 pl-5">
                  <li>
                    <span className="text-foreground font-medium">Include all sub-pages</span>{" "}
                    &mdash; grants access to every existing page in this section. New pages added
                    later will also inherit this access automatically.
                  </li>
                  <li>
                    <span className="text-foreground font-medium">This page only</span> &mdash; only
                    shares this specific page. Sub-pages and any new pages added later will not be
                    affected.
                  </li>
                </ul>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => handleRecursiveConfirm(false)}>
              This page only
            </AlertDialogCancel>
            <AlertDialogAction onClick={() => handleRecursiveConfirm(true)}>
              Include all sub-pages
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
