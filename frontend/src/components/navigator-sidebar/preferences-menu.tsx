"use client";

import { Settings } from "lucide-react";
import { toast } from "sonner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import { useDrives, usePreferences } from "@/hooks/use-navigator-tree";
import { useSetPreferences } from "@/hooks/use-navigator-mutations";

export function PreferencesMenu() {
  const drivesQuery = useDrives();
  const prefsQuery = usePreferences();
  const setPreferences = useSetPreferences();

  const showNonDocFiles = prefsQuery.data?.showNonDocFiles ?? true;
  const hiddenWorkspaceIds = prefsQuery.data?.hiddenWorkspaceIds ?? [];
  const hiddenSet = new Set(hiddenWorkspaceIds);

  const handleToggleNonDoc = async (next: boolean) => {
    try {
      await setPreferences.mutateAsync({ showNonDocFiles: next });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to update";
      toast.error(message);
    }
  };

  const handleToggleDrive = async (driveId: string, hidden: boolean) => {
    const nextHidden = hidden
      ? [...hiddenWorkspaceIds, driveId]
      : hiddenWorkspaceIds.filter((id) => id !== driveId);
    try {
      await setPreferences.mutateAsync({ hiddenWorkspaceIds: nextHidden });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to update";
      toast.error(message);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-6 items-center justify-center rounded-md outline-none"
          aria-label="Sidebar preferences"
        >
          <Settings className="size-3.5" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="text-xs">Display</DropdownMenuLabel>
        <DropdownMenuItem
          // `onSelect` instead of `onClick` keeps the menu open after
          // each toggle so the user can flip multiple prefs without
          // reopening. Same trick used for drive rows below.
          onSelect={(e) => e.preventDefault()}
          className="flex items-center justify-between gap-2"
        >
          <span className="text-sm">Show non-Doc files</span>
          <Switch checked={showNonDocFiles} onCheckedChange={handleToggleNonDoc} />
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs">Visible drives</DropdownMenuLabel>
        {(drivesQuery.data ?? []).length === 0 && (
          <div className="text-muted-foreground px-2 py-1 text-xs">No drives</div>
        )}
        {(drivesQuery.data ?? []).map((drive) => {
          const isHidden = hiddenSet.has(drive.id);
          return (
            <DropdownMenuItem
              key={drive.id}
              onSelect={(e) => e.preventDefault()}
              className="flex items-center justify-between gap-2"
            >
              <span className="truncate text-sm">{drive.name}</span>
              <Switch
                checked={!isHidden}
                onCheckedChange={(next) => handleToggleDrive(drive.id, !next)}
              />
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
