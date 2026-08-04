"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { memo } from "react";
import { useDebouncedCallback } from "use-debounce";

import { SidebarMenuButton } from "@/components/ui/sidebar";
import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { useSetDocIcon } from "@/hooks/use-wiki-mutations";
import { displayFileName, renderNodeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { runWithErrorToast } from "@/lib/mutation-toast";
import { markNav } from "@/lib/perf-marks";
import { type ResolvedShortcut, resolveShortcutTarget } from "@/lib/shortcut-resolver";
import { cn } from "@/lib/utils";
import { wikiKeys } from "@/lib/wiki-keys";
import { wikiReadApi } from "@/lib/wiki-read-client";
import type { DriveNode } from "@/types/wiki";
import { MIME_DOC, MIME_FORM } from "@/types/wiki";

import { ItemMenu } from "./item-menu";
import { CHEVRON_OFFSET, ICON_COLUMN_WIDTH, rowIndent } from "./layout-constants";
import { PickableIcon } from "./pickable-icon";
import { useIsMovingFile } from "./use-in-flight-moves";

interface FileRowProps {
  activeDriveId: null | string;
  depth: number;
  driveId: string | undefined;
  iconOverride: string | undefined;
  node: DriveNode;
  parentFolderId: string | undefined;
}

interface RowButtonProps {
  displayName: string;
  href: string;
  isActive: boolean;
  isForm: boolean;
  isMoving: boolean;
  linkPadding: string;
  node: DriveNode;
  onPrefetch: () => void;
  onPrewarm: () => void;
  onPrewarmCancel: () => void;
  route: ResolvedShortcut;
  titleAttr: string;
}

function FileRowImpl({
  activeDriveId,
  depth,
  driveId,
  iconOverride,
  node,
  parentFolderId,
}: FileRowProps) {
  const pathname = usePathname();
  const route = resolveShortcutTarget(node);

  const isForm = route.targetMimeType === MIME_FORM;
  const isDoc = !route.isShortcut && node.mimeType === MIME_DOC;
  const href = isForm
    ? openUrlFor(route.targetMimeType, route.targetId)
    : `/d/${encodeURIComponent(route.targetId)}`;
  const isActive = !isForm && (activeDriveId === route.targetId || pathname === href);
  const isMoving = useIsMovingFile(node.id);
  const displayName = displayFileName(node.name, route.targetMimeType);

  const queryClient = useQueryClient();
  const prefetchFile = (): void => {
    void queryClient.prefetchQuery({
      queryFn: async () => await wikiReadApi.getFile(route.targetId),
      queryKey: wikiKeys.file(route.targetId),
    });
  };

  const setPrewarm = useDocViewerStore((s) => s.setPrewarm);
  const prewarmNow = (): void => {
    if (isForm || route.inaccessible) return;
    setPrewarm({
      fileId: route.targetId,
      mimeType: route.isShortcut ? route.targetMimeType : node.mimeType,
      reloadKey: 0,
      title: displayName,
    });
  };
  const schedulePrewarm = useDebouncedCallback(prewarmNow, 120);
  const handleCommit = (): void => {
    markNav(route.targetId, "click");
    prefetchFile();
    prewarmNow();
  };

  const setDocIcon = useSetDocIcon();
  const handleIconChange = async (next: null | string) => {
    await runWithErrorToast(async () => {
      await setDocIcon.mutateAsync({ driveId: node.id, icon: next });
    }, "Failed to update icon");
  };

  const iconContent =
    iconOverride && !route.isShortcut ? (
      <span className="text-base leading-none">{iconOverride}</span>
    ) : (
      renderNodeIcon(node, "size-4 shrink-0")
    );

  const iconLeft = `${rowIndent(depth) + CHEVRON_OFFSET}px`;
  const linkPadding = `${rowIndent(depth) + CHEVRON_OFFSET + ICON_COLUMN_WIDTH}px`;
  const edited = node.modifiedTime
    ? ` — edited ${new Date(node.modifiedTime).toLocaleDateString()}`
    : "";
  const titleAttr =
    route.tooltip ?? (isForm ? `${displayName} — opens in a new tab` : `${displayName}${edited}`);

  return (
    <>
      <RowButton
        displayName={displayName}
        href={href}
        isActive={isActive}
        isForm={isForm}
        isMoving={isMoving}
        linkPadding={linkPadding}
        node={node}
        onPrefetch={handleCommit}
        onPrewarm={schedulePrewarm}
        onPrewarmCancel={schedulePrewarm.cancel}
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
          onChange={handleIconChange}
          pickable={isDoc}
          value={iconOverride}
        >
          {iconContent}
        </PickableIcon>
      </div>

      {!route.isShortcut && !route.inaccessible && (
        <div className="absolute top-1/2 right-1 flex -translate-y-1/2 items-center opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
          <ItemMenu
            currentName={node.name}
            driveId={driveId}
            fileId={node.id}
            isFolder={false}
            parentFolderId={parentFolderId}
          />
        </div>
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
  href,
  isActive,
  isForm,
  isMoving,
  linkPadding,
  node,
  onPrefetch,
  onPrewarm,
  onPrewarmCancel,
  route,
  titleAttr,
}: RowButtonProps) {
  if (route.inaccessible) {
    return (
      <SidebarMenuButton
        className={cn("text-muted-foreground/60 cursor-not-allowed pr-8", isMoving && "opacity-60")}
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
      isActive={isActive}
      style={{ paddingLeft: linkPadding }}
    >
      <Link
        draggable={false}
        href={`/d/${encodeURIComponent(route.targetId)}`}
        onBlur={onPrewarmCancel}
        onFocus={onPrewarm}
        onPointerDown={onPrefetch}
        onPointerEnter={onPrewarm}
        onPointerLeave={onPrewarmCancel}
        title={titleAttr}
      >
        <span className="truncate">{displayName}</span>
      </Link>
    </SidebarMenuButton>
  );
}

export const FileRow = memo(FileRowImpl);
