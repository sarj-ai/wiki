"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { memo } from "react";

import type { DriveNode } from "@/types/wiki";

import { SidebarMenuButton } from "@/components/ui/sidebar";
import { useSetDocIcon } from "@/hooks/use-wiki-mutations";
import { renderNodeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { runWithErrorToast } from "@/lib/mutation-toast";
import { cn } from "@/lib/utils";
import { wikiApi } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";
import { MIME_DOC, MIME_FORM, MIME_SHORTCUT } from "@/types/wiki";

import { ItemMenu } from "./item-menu";
import { ICON_COLUMN_WIDTH, rowIndent } from "./layout-constants";
import { PickableIcon } from "./pickable-icon";
import { useIsMovingFile } from "./use-in-flight-moves";

interface FileRowProps {
  node: DriveNode;
  driveId: string | undefined;
  parentFolderId: string | undefined;
  activeDriveId: string | null;
  iconOverride: string | undefined;
  depth: number;
}

interface OpenRoute {
  isShortcut: boolean;
  inaccessible: boolean;
  mimeType: string;
  id: string;
  tooltip: string | null;
}

interface RowButtonProps {
  node: DriveNode;
  route: OpenRoute;
  href: string;
  isActive: boolean;
  isForm: boolean;
  isMoving: boolean;
  linkPadding: string;
  titleAttr: string;
  onPrefetch: () => void;
}

// Compute the routing/labeling target for a row. Shortcuts forward to
// their target so opens, breadcrumbs, and active-state highlighting all
// agree on the same id and mime — the host pointer is never what the
// user wants to open.
function resolveOpenRoute(node: DriveNode): OpenRoute {
  if (node.mimeType !== MIME_SHORTCUT) {
    return {
      isShortcut: false,
      inaccessible: false,
      mimeType: node.mimeType,
      id: node.id,
      tooltip: null,
    };
  }
  const inaccessible = node.shortcutInaccessible;
  const tooltip = inaccessible
    ? "You don't have access to this shortcut's target. Contact your admin."
    : node.shortcutTargetDriveName
      ? `Shortcut → ${node.shortcutTargetDriveName}`
      : "Shortcut";
  return {
    isShortcut: true,
    inaccessible,
    mimeType: node.shortcutTargetMimeType ?? "",
    id: node.shortcutTargetId ?? node.id,
    tooltip,
  };
}

function FileRowImpl({
  node,
  driveId,
  parentFolderId,
  activeDriveId,
  iconOverride,
  depth,
}: FileRowProps) {
  const pathname = usePathname();
  const route = resolveOpenRoute(node);
  // Google Forms' /edit view can't be embedded in a cross-origin iframe
  // — the editor hard-gates with a "You need access" page regardless of
  // which API created the form or which account owns it. Opening Forms
  // in a top-level tab is the only working path.
  const isForm = route.mimeType === MIME_FORM;
  const isDoc = !route.isShortcut && node.mimeType === MIME_DOC;
  const href = isForm ? openUrlFor(route.mimeType, route.id) : `/d/${encodeURIComponent(route.id)}`;
  const isActive = !isForm && (activeDriveId === route.id || pathname === href);
  const isMoving = useIsMovingFile(node.id);

  const queryClient = useQueryClient();
  const prefetchFile = (): void => {
    void queryClient.prefetchQuery({
      queryKey: wikiKeys.file(route.id),
      queryFn: () => wikiApi.getFile(route.id),
    });
  };

  const setDocIcon = useSetDocIcon();
  const handleIconChange = async (next: string | null) => {
    await runWithErrorToast(async () => {
      await setDocIcon.mutateAsync({ driveId: node.id, icon: next });
    }, "Failed to update icon");
  };

  // Shortcuts ignore icon overrides — the doc_icons row belongs to the
  // target, and we'd write to the wrong key if we exposed the picker
  // here. Render the target's mime icon plus the shortcut overlay glyph.
  const iconContent =
    iconOverride && !route.isShortcut ? (
      <span className="text-base leading-none">{iconOverride}</span>
    ) : (
      renderNodeIcon(node, "size-4 shrink-0")
    );

  // Icon sits OUTSIDE the <Link> as an absolutely-positioned overlay
  // on top of the SidebarMenuButton. Clicking it never bubbles into
  // an anchor element, so there's no risk of Next.js Link navigating
  // on a supposedly "icon-only" click. The Link's left padding is
  // bumped by ICON_COLUMN_WIDTH so the title text stays readable.
  const iconLeft = `${rowIndent(depth)}px`;
  const linkPadding = `${rowIndent(depth) + ICON_COLUMN_WIDTH}px`;
  const titleAttr = route.tooltip ?? (isForm ? `${node.name} — opens in a new tab` : node.name);

  return (
    <>
      <RowButton
        node={node}
        route={route}
        href={href}
        isActive={isActive}
        isForm={isForm}
        isMoving={isMoving}
        linkPadding={linkPadding}
        titleAttr={titleAttr}
        onPrefetch={prefetchFile}
      />
      {/*
        Doc icons open the EmojiPickerPopover; non-Doc icons are
        decorative — ``pointer-events-none`` lets clicks fall through
        to the Link underneath so tapping the icon also opens the
        file. Without this, the absolute overlay swallows the click
        and nothing happens.
      */}
      <div
        className={cn(
          "absolute top-1/2 z-10 -translate-y-1/2",
          !isDoc && "pointer-events-none",
          route.inaccessible && "opacity-50",
        )}
        style={{ left: iconLeft }}
      >
        <PickableIcon
          pickable={isDoc}
          value={iconOverride}
          onChange={handleIconChange}
          ariaLabel="Change icon"
        >
          {iconContent}
        </PickableIcon>
      </div>
      {/*
        Shortcut rows hide the rename / move / delete actions — they'd
        operate on the *shortcut* (renaming the pointer in Sales) rather
        than the doc users see, which is rarely what anyone wants. Open
        Drive directly to manage shortcut entries.
      */}
      {!route.isShortcut && !route.inaccessible && (
        <div className="absolute top-1/2 right-1 flex -translate-y-1/2 items-center opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
          <ItemMenu
            fileId={node.id}
            driveId={driveId}
            parentFolderId={parentFolderId}
            currentName={node.name}
            isFolder={false}
          />
        </div>
      )}
      {isMoving && (
        <div className="text-muted-foreground pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
          <Loader2 className="size-3 animate-spin" />
        </div>
      )}
    </>
  );
}

// Inaccessible shortcuts render as a non-interactive disabled row —
// we can't navigate to a target the user isn't allowed to read, and
// the iframe would just show Google's "You need access" page. The
// branching lives here so the parent stays under the cognitive
// complexity budget.
function RowButton({
  node,
  route,
  href,
  isActive,
  isForm,
  isMoving,
  linkPadding,
  titleAttr,
  onPrefetch,
}: RowButtonProps) {
  if (route.inaccessible) {
    return (
      <SidebarMenuButton
        disabled
        className={cn("text-muted-foreground/60 cursor-not-allowed pr-8", isMoving && "opacity-60")}
        style={{ paddingLeft: linkPadding }}
        title={titleAttr}
      >
        <span className="truncate">{node.name}</span>
      </SidebarMenuButton>
    );
  }
  if (isForm) {
    return (
      <SidebarMenuButton
        asChild
        isActive={isActive}
        className={cn("pr-8 transition-opacity", isMoving && "opacity-60")}
        style={{ paddingLeft: linkPadding }}
        data-file-id={node.id}
      >
        <a
          href={href}
          draggable={false}
          title={titleAttr}
          target="_blank"
          rel="noopener noreferrer"
        >
          <span className="truncate">{node.name}</span>
          <ExternalLink
            className="text-muted-foreground ml-auto size-3 shrink-0"
            aria-label="Opens in a new tab"
          />
        </a>
      </SidebarMenuButton>
    );
  }
  return (
    <SidebarMenuButton
      asChild
      isActive={isActive}
      className={cn("pr-8 transition-opacity", isMoving && "opacity-60")}
      style={{ paddingLeft: linkPadding }}
      data-file-id={node.id}
    >
      <Link
        href={`/d/${encodeURIComponent(route.id)}`}
        // HTML5 native anchor drag is on by default. Chrome treats
        // drag-and-drop of an anchor back onto the same page as a
        // navigation to ``href`` — bypassing click handlers entirely.
        // That was the path through which dnd-kit reorders landed the
        // user on the dragged file's URL. Disabling native drag lets
        // dnd-kit's PointerSensor own the drag lifecycle cleanly.
        draggable={false}
        title={titleAttr}
        onPointerDown={onPrefetch}
      >
        <span className="truncate">{node.name}</span>
      </Link>
    </SidebarMenuButton>
  );
}

export const FileRow = memo(FileRowImpl);
