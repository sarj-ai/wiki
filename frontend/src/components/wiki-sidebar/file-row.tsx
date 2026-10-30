"use client";

import { ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { memo } from "react";

import { SidebarMenuButton } from "@/components/ui/sidebar";
import { displayFileName, renderNodeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { markNav } from "@/lib/perf-marks";
import { type ResolvedShortcut, resolveShortcutTarget } from "@/lib/shortcut-resolver";
import { cn } from "@/lib/utils";
import type { DriveNode } from "@/types/wiki";
import { MIME_DOC, MIME_FORM } from "@/types/wiki";

import { ItemMenu } from "./item-menu";
import { CHEVRON_OFFSET, ICON_COLUMN_WIDTH, rowIndent } from "./layout-constants";
import { PickableIcon } from "./pickable-icon";
import { RowActions } from "./row-actions";
import { useDocumentIntent } from "./use-document-intent";
import { useIsMovingFile } from "./use-in-flight-moves";
import { useSidebarNavigation } from "./use-sidebar-navigation";

const MODIFIED_DATE_FORMAT = new Intl.DateTimeFormat("en", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
  year: "numeric",
});

interface FileRowProps {
  activeDriveId: null | string;
  depth: number;
  driveId: string | undefined;
  iconOverride: string | undefined;
  node: DriveNode;
  parentFolderId: string | undefined;
  pathname?: string | undefined;
}

interface RowButtonProps {
  displayName: string;
  documentHref: `/d/${string}`;
  href: string;
  intent: ReturnType<typeof useDocumentIntent>;
  isActive: boolean;
  isForm: boolean;
  isMoving: boolean;
  linkPadding: string;
  node: DriveNode;
  route: ResolvedShortcut;
  titleAttr: string;
}

function FileRowImpl(props: FileRowProps) {
  if (props.pathname === undefined) return <FileRowWithPathname {...props} />;
  return <FileRowContent {...props} pathname={props.pathname} />;
}

function FileRowWithPathname(props: FileRowProps) {
  const pathname = usePathname();
  return <FileRowContent {...props} pathname={pathname} />;
}

function FileRowContent({
  activeDriveId,
  depth,
  driveId,
  iconOverride,
  node,
  parentFolderId,
  pathname,
}: FileRowProps & { pathname: string }) {
  const route = resolveShortcutTarget(node);

  const isForm = route.targetMimeType === MIME_FORM;
  const isDoc = !route.isShortcut && node.mimeType === MIME_DOC;
  const documentHref: `/d/${string}` = `/d/${encodeURIComponent(route.targetId)}`;
  const href = isForm ? openUrlFor(route.targetMimeType, route.targetId) : documentHref;
  const isActive = !isForm && (activeDriveId === route.targetId || pathname === href);
  const isMoving = useIsMovingFile(node.id);
  const displayName = displayFileName(node.name, route.targetMimeType);

  const intent = useDocumentIntent({
    disabled: isForm || route.inaccessible || isActive,
    fileId: route.targetId,
    onStrongIntent: () => {
      markNav(route.targetId, "click");
    },
  });

  const iconContent =
    iconOverride && !route.isShortcut ? (
      <span className="text-base leading-none">{iconOverride}</span>
    ) : (
      renderNodeIcon(node, "size-4 shrink-0")
    );

  const iconLeft = `${rowIndent(depth) + CHEVRON_OFFSET}px`;
  const linkPadding = `${rowIndent(depth) + CHEVRON_OFFSET + ICON_COLUMN_WIDTH}px`;
  const edited =
    !route.tooltip && !isForm && node.modifiedTime
      ? ` — edited ${MODIFIED_DATE_FORMAT.format(new Date(node.modifiedTime))}`
      : "";
  const titleAttr =
    route.tooltip ?? (isForm ? `${displayName} — opens in a new tab` : `${displayName}${edited}`);

  return (
    <>
      <RowButton
        displayName={displayName}
        documentHref={documentHref}
        href={href}
        intent={intent}
        isActive={isActive}
        isForm={isForm}
        isMoving={isMoving}
        linkPadding={linkPadding}
        node={node}
        route={route}
        titleAttr={titleAttr}
      />

      <div
        className={cn(
          "absolute top-1/2 z-10 -translate-y-1/2",
          !isDoc && "pointer-events-none",
          route.inaccessible && "opacity-50",
        )}
        style={{ left: iconLeft }}
      >
        <PickableIcon
          ariaLabel="Change icon"
          pickable={isDoc}
          target={{ id: node.id, kind: "doc" }}
          value={iconOverride}
        >
          {iconContent}
        </PickableIcon>
      </div>

      {!route.isShortcut && !route.inaccessible && (
        <RowActions>
          <ItemMenu
            currentName={node.name}
            driveId={driveId}
            fileId={node.id}
            isFolder={false}
            parentFolderId={parentFolderId}
          />
        </RowActions>
      )}
      {isMoving ? (
        <div className="text-muted-foreground pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
          <Loader2 className="size-3 animate-spin" />
        </div>
      ) : null}
    </>
  );
}

function RowButton({
  displayName,
  documentHref,
  href,
  isActive,
  isForm,
  isMoving,
  intent,
  linkPadding,
  node,
  route,
  titleAttr,
}: RowButtonProps) {
  const navigateSidebar = useSidebarNavigation();
  if (route.inaccessible) {
    return (
      <SidebarMenuButton
        className={cn("text-muted-foreground/60 cursor-not-allowed pr-8", isMoving && "opacity-60")}
        data-sidebar-row="file"
        disabled
        style={{ paddingLeft: linkPadding }}
        title={titleAttr}
      >
        <span className="truncate">{displayName}</span>
      </SidebarMenuButton>
    );
  }
  if (isForm) {
    return (
      <SidebarMenuButton
        asChild
        className={cn("pr-8 transition-opacity", isMoving && "opacity-60")}
        data-file-id={node.id}
        data-sidebar-row="file"
        isActive={isActive}
        style={{ paddingLeft: linkPadding }}
      >
        <a
          draggable={false}
          href={href}
          rel="noopener noreferrer"
          target="_blank"
          title={titleAttr}
        >
          <span className="truncate">{displayName}</span>
          <ExternalLink
            aria-label="Opens in a new tab"
            className="text-muted-foreground ml-auto size-3 shrink-0"
          />
        </a>
      </SidebarMenuButton>
    );
  }
  return (
    <SidebarMenuButton
      asChild
      className={cn("pr-8 transition-opacity", isMoving && "opacity-60")}
      data-file-id={node.id}
      data-sidebar-row="file"
      isActive={isActive}
      style={{ paddingLeft: linkPadding }}
    >
      <Link
        draggable={false}
        href={documentHref}
        onBlur={intent.onBlur}
        onClick={intent.onClick}
        onFocus={intent.onFocus}
        onNavigate={() => navigateSidebar(documentHref)}
        onPointerDown={intent.onPointerDown}
        onPointerEnter={intent.onPointerEnter}
        onPointerLeave={intent.onPointerLeave}
        prefetch={false}
        title={titleAttr}
      >
        <span className="truncate">{displayName}</span>
      </Link>
    </SidebarMenuButton>
  );
}

export const FileRow = memo(FileRowImpl);
