"use client";

import {
  Building2,
  ExternalLink,
  Globe2,
  Link2,
  Loader2,
  RefreshCw,
  UserRound,
  UsersRound,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useDrivePermissions } from "@/hooks/use-wiki-tree";
import { folderUrlFor } from "@/lib/file-urls";
import type {
  DrivePermission,
  DrivePermissionsPage,
  DrivePrincipalType,
  DriveSummary,
} from "@/types/wiki";

const ROLE_ORDER = new Map([
  ["organizer", 0],
  ["owner", 0],
  ["fileOrganizer", 1],
  ["writer", 2],
  ["commenter", 3],
  ["reader", 4],
]);

const ROLE_LABELS: Record<string, string> = {
  commenter: "Commenter",
  fileOrganizer: "Content manager",
  organizer: "Manager",
  owner: "Owner",
  reader: "Viewer",
  writer: "Contributor",
};

export function DriveAccessSheet({ drive }: { drive: DriveSummary }) {
  const [open, setOpen] = useState(false);

  return (
    <Sheet onOpenChange={setOpen} open={open}>
      <SheetTrigger asChild>
        <Button
          aria-label={`View access for ${drive.name}`}
          className="size-5 shrink-0"
          size="icon-xs"
          title={`View access for ${drive.name}`}
          type="button"
          variant="ghost"
        >
          <UsersRound className="size-3.5" />
        </Button>
      </SheetTrigger>
      <SheetContent className="w-[min(24rem,calc(100vw-1rem))] gap-0 sm:max-w-sm">
        <DriveAccessContent drive={drive} open={open} />
      </SheetContent>
    </Sheet>
  );
}

function DriveAccessContent({ drive, open }: { drive: DriveSummary; open: boolean }) {
  const query = useDrivePermissions(drive.isPersonal ? null : drive.id, open && !drive.isPersonal);
  const { count, generalAccess, people } = derivePermissions(query.data?.pages ?? []);

  return (
    <>
      <SheetHeader className="border-b pb-4">
        <div className="flex items-center gap-2 pr-8">
          <div className="bg-primary/10 text-primary flex size-8 items-center justify-center rounded-full">
            <UsersRound className="size-4" />
          </div>
          <div className="min-w-0">
            <SheetTitle className="truncate">{drive.name} access</SheetTitle>
            <SheetDescription aria-live="polite">
              {drive.isPersonal
                ? "Sharing is managed on each file and folder."
                : query.isPending
                  ? "Loading access…"
                  : query.isError
                    ? "Couldn't load drive access"
                    : permissionSummary(count)}
            </SheetDescription>
          </div>
        </div>
      </SheetHeader>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {drive.isPersonal ? (
          <PersonalDriveNotice driveId={drive.id} />
        ) : query.isPending ? (
          <AccessSkeleton />
        ) : query.isError ? (
          <AccessError
            onRetry={() => {
              void query.refetch();
            }}
          />
        ) : (
          <div className="space-y-5">
            {generalAccess.length > 0 ? (
              <AccessSection label="General access">
                {generalAccess.map((permission) => (
                  <PermissionRow key={permission.id} permission={permission} />
                ))}
              </AccessSection>
            ) : null}

            <AccessSection label="People and groups">
              {people.length === 0 ? (
                <p className="text-muted-foreground px-1 py-3 text-sm">
                  Google Drive returned no members for this drive.
                </p>
              ) : (
                people.map((permission) => (
                  <PermissionRow key={permission.id} permission={permission} />
                ))
              )}
            </AccessSection>

            {query.hasNextPage ? (
              <Button
                className="w-full"
                disabled={query.isFetchingNextPage}
                onClick={() => {
                  void query.fetchNextPage();
                }}
                type="button"
                variant="outline"
              >
                {query.isFetchingNextPage ? <Loader2 className="size-4 animate-spin" /> : null}
                {query.isFetchingNextPage ? "Loading…" : "Load more people"}
              </Button>
            ) : null}
          </div>
        )}
      </div>

      {!drive.isPersonal ? (
        <div className="border-t p-4">
          <Button asChild className="w-full" variant="outline">
            <a href={folderUrlFor(drive.id)} rel="noopener noreferrer" target="_blank">
              <ExternalLink className="size-4" />
              Manage in Google Drive
            </a>
          </Button>
        </div>
      ) : null}
    </>
  );
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role.replaceAll(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
}

function AccessSection({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <section>
      <h3 className="text-muted-foreground mb-2 px-1 text-[11px] font-semibold tracking-wide uppercase">
        {label}
      </h3>
      <div className="divide-border/70 divide-y rounded-lg border">{children}</div>
    </section>
  );
}

function PermissionRow({ permission }: { permission: DrivePermission }) {
  const broad = permission.principalType === "domain" || permission.principalType === "anyone";
  return (
    <div className="flex min-h-14 items-center gap-3 px-3 py-2.5">
      <div
        className={
          broad
            ? "bg-muted text-foreground rounded-full"
            : "bg-muted text-muted-foreground rounded-full"
        }
      >
        <div className="flex size-8 items-center justify-center rounded-full">
          <PrincipalIcon type={permission.principalType} />
        </div>
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{principalName(permission)}</div>
        <div className="text-muted-foreground truncate text-xs">{principalDetail(permission)}</div>
      </div>
      <div className="text-muted-foreground shrink-0 pl-2 text-xs font-medium">
        {roleLabel(permission.role)}
      </div>
    </div>
  );
}

function PrincipalIcon({ type }: { type: DrivePrincipalType }) {
  if (type === "group") return <UsersRound className="size-4" />;
  if (type === "domain") return <Building2 className="size-4" />;
  if (type === "anyone") return <Globe2 className="size-4" />;
  return <UserRound className="size-4" />;
}

function PersonalDriveNotice({ driveId }: { driveId: string }) {
  return (
    <div className="space-y-4">
      <div className="bg-muted/50 rounded-lg border p-4">
        <div className="mb-2 flex items-center gap-2 font-medium">
          <Link2 className="size-4" />
          Access varies by item
        </div>
        <p className="text-muted-foreground text-sm leading-relaxed">
          My Drive has no single member list. Open a file or folder in Google Drive to see who can
          access that item.
        </p>
      </div>
      <Button asChild className="w-full" variant="outline">
        <a href={folderUrlFor(driveId)} rel="noopener noreferrer" target="_blank">
          <ExternalLink className="size-4" />
          Open My Drive
        </a>
      </Button>
    </div>
  );
}

function AccessSkeleton() {
  return (
    <div className="space-y-3">
      {["one", "two", "three", "four"].map((key) => (
        <div className="flex items-center gap-3" key={key}>
          <Skeleton className="size-8 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-2/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-3 w-14" />
        </div>
      ))}
    </div>
  );
}

function AccessError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="border-destructive/30 bg-destructive/5 rounded-lg border p-4">
      <p className="text-destructive text-sm font-medium">Couldn&apos;t load drive access</p>
      <p className="text-muted-foreground mt-1 text-sm">
        Google Drive may not allow your role to view the member list.
      </p>
      <Button className="mt-3" onClick={onRetry} size="sm" type="button" variant="outline">
        <RefreshCw className="size-3.5" />
        Retry
      </Button>
    </div>
  );
}

export function derivePermissions(pages: readonly DrivePermissionsPage[]) {
  const byId = new Map<string, DrivePermission>();
  for (const page of pages) {
    for (const permission of page.permissions) byId.set(permission.id, permission);
  }
  const generalAccess: DrivePermission[] = [];
  const people: DrivePermission[] = [];
  for (const permission of byId.values()) {
    const type = permission.principalType;
    if (type === "domain" || type === "anyone") generalAccess.push(permission);
    else people.push(permission);
  }
  return { count: byId.size, generalAccess, people: people.toSorted(comparePermissions) };
}

function comparePermissions(a: DrivePermission, b: DrivePermission): number {
  const roleDifference = (ROLE_ORDER.get(a.role) ?? 99) - (ROLE_ORDER.get(b.role) ?? 99);
  if (roleDifference !== 0) return roleDifference;
  return principalName(a).localeCompare(principalName(b));
}

function principalName(permission: DrivePermission): string {
  if (permission.deleted) return "Deleted account";
  if (permission.displayName) return permission.displayName;
  if (permission.emailAddress) return permission.emailAddress;
  if (permission.domain) return permission.domain;
  if (permission.principalType === "anyone") return "Anyone with the link";
  return permission.principalType === "group" ? "Unknown group" : "Unknown user";
}

function principalDetail(permission: DrivePermission): string {
  if (permission.pendingOwner) return "Ownership pending";
  if (permission.expirationTime)
    return `Expires ${new Date(permission.expirationTime).toLocaleDateString()}`;
  if (permission.emailAddress && permission.emailAddress !== permission.displayName) {
    return permission.emailAddress;
  }
  if (permission.principalType === "domain") {
    return permission.allowFileDiscovery
      ? "Domain access · discoverable"
      : "Domain access · link only";
  }
  if (permission.principalType === "anyone") {
    return permission.allowFileDiscovery ? "Public on the web" : "Anyone with the link";
  }
  return permission.principalType === "group" ? "Google group" : "Google account";
}

function permissionSummary(count: number): string {
  if (count === 0) return "No access entries returned";
  return `${count} access ${count === 1 ? "entry" : "entries"}`;
}
