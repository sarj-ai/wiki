"use client";

import { Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useSetPreferences } from "@/hooks/use-wiki-mutations";
import { useDrives, usePreferences } from "@/hooks/use-wiki-tree";
import { runWithErrorToast } from "@/lib/mutation-toast";

import { DriveListFeedback } from "./drive-list-feedback";

export function PreferencesDialog({
  onCloseAutoFocus,
  onOpenAutoFocus,
  onOpenChange,
  open,
}: PreferencesDialogProps) {
  const drivesQuery = useDrives();
  const prefsQuery = usePreferences();
  const setPreferences = useSetPreferences();

  const hasNoDrives = drivesQuery.isSuccess && drivesQuery.data.length === 0;
  const hasPreferences = prefsQuery.data !== undefined;
  const showNonDocFiles = prefsQuery.data?.showNonDocFiles ?? true;
  const hiddenWorkspaceIds = prefsQuery.data?.hiddenWorkspaceIds ?? [];
  const hiddenSet = new Set(hiddenWorkspaceIds);

  const updatePrefs = (payload: Parameters<typeof setPreferences.mutateAsync>[0]) => {
    if (!hasPreferences) return;
    void runWithErrorToast(async () => {
      await setPreferences.mutateAsync(payload);
    }, "Failed to update preferences").catch(() =>
      console.warn("Preference update could not finish"),
    );
  };

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        className="max-h-[calc(100dvh-1rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-sm"
        onCloseAutoFocus={onCloseAutoFocus}
        onOpenAutoFocus={onOpenAutoFocus}
      >
        <DialogHeader className="px-6 pt-6 pr-12">
          <DialogTitle className="flex items-center gap-2">
            <Settings className="size-4" />
            Preferences
          </DialogTitle>
          <DialogDescription>
            Controls what the sidebar shows. All settings are per-user.
          </DialogDescription>
        </DialogHeader>

        <div
          aria-label="Preferences settings"
          className="min-h-0 space-y-4 overflow-y-auto px-6 py-4"
          role="region"
          tabIndex={0}
        >
          {prefsQuery.isPending ? (
            <p className="text-muted-foreground p-2 text-sm" role="status">
              Loading preferences…
            </p>
          ) : null}
          {prefsQuery.isError ? (
            <div className="space-y-2 p-2 text-sm" role="alert">
              <p>Preferences could not be loaded.</p>
              <Button
                disabled={prefsQuery.isFetching}
                onClick={() => {
                  void prefsQuery
                    .refetch()
                    .catch(() => console.warn("Preference retry could not finish"));
                }}
                size="sm"
                type="button"
                variant="outline"
              >
                Retry preferences
              </Button>
            </div>
          ) : null}
          <section>
            <div className="text-muted-foreground mb-1 px-2 text-xs font-medium tracking-wide uppercase">
              Display
            </div>
            <CheckboxRow
              checked={showNonDocFiles}
              description="Include Sheets, Slides, Forms, Drawings, PDFs and images in the sidebar."
              disabled={!hasPreferences}
              id="pref-show-non-doc"
              label="Show non-Doc files"
              onChange={(next) => {
                updatePrefs({ showNonDocFiles: next });
              }}
            />
          </section>

          <section>
            <div className="text-muted-foreground mb-1 px-2 text-xs font-medium tracking-wide uppercase">
              Visible drives
            </div>
            <DriveListFeedback empty={hasNoDrives} query={drivesQuery} />
            <div className="space-y-0.5">
              {(drivesQuery.data ?? []).map((drive) => {
                const isHidden = hiddenSet.has(drive.id);
                return (
                  <CheckboxRow
                    checked={!isHidden}
                    disabled={!hasPreferences}
                    id={`pref-drive-${drive.id}`}
                    key={drive.id}
                    label={drive.name}
                    onChange={(next) => {
                      const nextHidden = next
                        ? hiddenWorkspaceIds.filter((id) => id !== drive.id)
                        : [...hiddenWorkspaceIds, drive.id];
                      updatePrefs({ hiddenWorkspaceIds: nextHidden });
                    }}
                  />
                );
              })}
            </div>
          </section>
        </div>

        <DialogFooter className="flex-row items-center border-t px-6 py-4">
          {setPreferences.isPending ? (
            <p aria-live="polite" className="text-muted-foreground mr-auto text-xs" role="status">
              Saving preferences…
            </p>
          ) : null}
          <DialogClose asChild>
            <Button type="button">Done</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface PreferencesDialogProps {
  onCloseAutoFocus?: (event: Event) => void;
  onOpenAutoFocus?: (event: Event) => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}

function CheckboxRow({
  checked,
  description,
  disabled,
  id,
  label,
  onChange,
}: {
  id: string;
  label: string;
  description?: string;
  disabled: boolean;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label
      className="hover:bg-accent/50 flex cursor-pointer items-start gap-3 rounded-md p-2 text-sm"
      htmlFor={id}
    >
      <Checkbox
        checked={checked}
        className="mt-0.5"
        disabled={disabled}
        id={id}
        onCheckedChange={(next) => {
          onChange(next === true);
        }}
      />
      <div className="min-w-0 flex-1">
        <div className="font-medium wrap-anywhere">{label}</div>
        {description ? <div className="text-muted-foreground text-xs">{description}</div> : null}
      </div>
    </label>
  );
}
