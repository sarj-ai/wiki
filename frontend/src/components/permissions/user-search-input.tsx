"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import type { Permission, PagePermission, UserSearchResult } from "@/types";
import { useLoading } from "@/components/loading-bar";
import { api } from "@/lib/api";
import { Input } from "@/components/ui/input";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/components/ui/avatar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Loader2 } from "lucide-react";
import { getInitials } from "./utils";

interface UserSearchInputProps {
  pageId: string;
  permissions: PagePermission[];
  onSelectUser: (newPermission: PagePermission) => void;
}

export function UserSearchInput({
  pageId,
  permissions,
  onSelectUser,
}: UserSearchInputProps) {
  const { start, done } = useLoading();

  const [searchQuery, setSearchQuery] = useState("");
  const [invitePermission, setInvitePermission] = useState<Permission>("view");
  const [suggestions, setSuggestions] = useState<UserSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [grantingUserId, setGrantingUserId] = useState<string | null>(null);
  const searchRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const searchCounterRef = useRef(0);

  // Debounced user search with request counter to prevent stale results
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
          const results = await api.searchUsers(value.trim());
          // Only apply results if this is still the latest request
          if (requestId !== searchCounterRef.current) return;
          // Filter out users who already have permission on this page
          const existingUserIds = new Set(
            permissions.filter((p) => p.userId).map((p) => p.userId)
          );
          const filtered = results.filter((u) => !existingUserIds.has(u.id));
          setSuggestions(filtered);
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
    [permissions]
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

  async function handleSelectUser(selectedUser: UserSearchResult) {
    setGrantingUserId(selectedUser.id);
    start();
    try {
      const newPerm = await api.grantPermission({
        pageId,
        userId: selectedUser.id,
        permission: invitePermission,
      });
      onSelectUser(newPerm);
      setSearchQuery("");
      setSuggestions([]);
      setShowSuggestions(false);
    } catch {
      // Could show error toast
    } finally {
      setGrantingUserId(null);
      done();
    }
  }

  return (
    <div className="flex gap-2">
      <div className="relative flex-1" ref={searchRef}>
        <Input
          placeholder="Search people by name or email..."
          value={searchQuery}
          onChange={(e) => handleSearchChange(e.target.value)}
          onFocus={() => {
            if (suggestions.length > 0) setShowSuggestions(true);
          }}
          className="h-9"
        />
        {showSuggestions && (
          <div className="absolute top-full left-0 right-0 z-50 mt-1 rounded-md border bg-popover p-1 shadow-md max-h-[200px] overflow-y-auto">
            {isSearching && (
              <div className="flex items-center justify-center py-3">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            )}
            {!isSearching &&
              suggestions.length === 0 &&
              searchQuery.trim().length >= 2 && (
                <p className="px-2 py-2 text-sm text-muted-foreground">
                  No users found
                </p>
              )}
            {suggestions.map((s) => (
              <button
                key={s.id}
                type="button"
                disabled={grantingUserId === s.id}
                onClick={() => handleSelectUser(s)}
                className="flex w-full items-center gap-3 rounded-sm px-2 py-2 text-left hover:bg-accent transition-colors disabled:opacity-50"
              >
                <Avatar className="size-7">
                  {s.avatarUrl && (
                    <AvatarImage
                      src={s.avatarUrl}
                      alt={s.displayName ?? ""}
                    />
                  )}
                  <AvatarFallback className="text-xs">
                    {getInitials(s.displayName ?? s.email)}
                  </AvatarFallback>
                </Avatar>
                <div className="flex flex-col min-w-0 flex-1">
                  <span className="text-sm font-medium truncate">
                    {s.displayName ?? s.email}
                  </span>
                  <span className="text-xs text-muted-foreground truncate">
                    {s.email}
                  </span>
                </div>
                {grantingUserId === s.id && (
                  <Loader2 className="size-4 animate-spin text-muted-foreground shrink-0" />
                )}
              </button>
            ))}
          </div>
        )}
      </div>
      <Select
        value={invitePermission}
        onValueChange={(v) => setInvitePermission(v as Permission)}
      >
        <SelectTrigger className="w-[110px] h-9" size="sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="view">Can view</SelectItem>
          <SelectItem value="edit">Can edit</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}
