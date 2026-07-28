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

interface PreferencesDialogProps {
  onOpenChange: (open: boolean) => void;
  open: boolean;
}

function CheckboxRow({
  checked,
  description,
  id,
  label,
  onChange,
}: {
  id: string;
  label: string;
  description?: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label
      className="hover:bg-accent/50 flex cursor-pointer items-start gap-3 rounded-md px-2 py-2 text-sm"
      htmlFor={id}
    >
      <Checkbox
        checked={checked}
        className="mt-0.5"
        id={id}
        onCheckedChange={(next) => {
          onChange(next === true);
        }}
      />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{label}</div>
        {description ? <div className="text-muted-foreground text-xs">{description}</div> : null}
      </div>
    </label>
  );
}

export function PreferencesDialog({ onOpenChange, open }: PreferencesDialogProps) {
  const drivesQuery = useDrives();
  const prefsQuery = usePreferences();
  const setPreferences = useSetPreferences();

  const showNonDocFiles = prefsQuery.data?.showNonDocFiles ?? true;
  const hiddenWorkspaceIds = prefsQuery.data?.hiddenWorkspaceIds ?? [];
  const hiddenSet = new Set(hiddenWorkspaceIds);

  const updatePrefs = async (payload: Parameters<typeof setPreferences.mutateAsync>[0]) => {
    await runWithErrorToast(async () => {
      await setPreferences.mutateAsync(payload);
    }, "Failed to update preferences");
  };

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings className="size-4" />
            Preferences
          </DialogTitle>
          <DialogDescription>
            Controls what the sidebar shows. All settings are per-user.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <section>
            <div className="text-muted-foreground mb-1 px-2 text-xs font-medium tracking-wide uppercase">
              Display
            </div>
            <CheckboxRow
              checked={showNonDocFiles}
              description="Include Sheets, Slides, Forms, Drawings, PDFs and images in the sidebar."
              id="pref-show-non-doc"
              label="Show non-Doc files"
              onChange={(next) => {
                void updatePrefs({ showNonDocFiles: next });
              }}
            />
          </section>

          <section>
            <div className="text-muted-foreground mb-1 px-2 text-xs font-medium tracking-wide uppercase">
              Visible drives
            </div>
            {(drivesQuery.data ?? []).length === 0 && (
              <div className="text-muted-foreground px-2 py-2 text-xs">No drives</div>
            )}
            <div className="space-y-0.5">
              {(drivesQuery.data ?? []).map((drive) => {
                const isHidden = hiddenSet.has(drive.id);
                return (
                  <CheckboxRow
                    checked={!isHidden}
                    id={`pref-drive-${drive.id}`}
                    key={drive.id}
                    label={drive.name}
                    onChange={(next) => {
                      const nextHidden = next
                        ? hiddenWorkspaceIds.filter((id) => id !== drive.id)
                        : [...hiddenWorkspaceIds, drive.id];
                      void updatePrefs({ hiddenWorkspaceIds: nextHidden });
                    }}
                  />
                );
              })}
            </div>
          </section>
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button type="button">Done</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
