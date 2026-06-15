"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { memo } from "react";
import { useDebouncedCallback } from "use-debounce";

import type { DriveNode } from "@/types/wiki";

import { SidebarMenuButton } from "@/components/ui/sidebar";
import { useDocViewerStore } from "@/components/wiki-document/doc-viewer-store";
import { useSetDocIcon } from "@/hooks/use-wiki-mutations";
import { renderNodeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { runWithErrorToast } from "@/lib/mutation-toast";
import { resolveShortcutTarget, type ResolvedShortcut } from "@/lib/shortcut-resolver";
import { cn } from "@/lib/utils";
import { wikiApi } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";
import { MIME_DOC, MIME_FORM } from "@/types/wiki";

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

interface RowButtonProps {
  node: DriveNode;
  route: ResolvedShortcut;
  href: string;
  isActive: boolean;
  isForm: boolean;
  isMoving: boolean;
  linkPadding: string;
  titleAttr: string;
  onPrefetch: () => void;
  onPrewarm: () => void;
  onPrewarmCancel: () => void;
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
  const route = resolveShortcutTarget(node);

  const isForm = route.targetMimeType === MIME_FORM;
  const isDoc = !route.isShortcut && node.mimeType === MIME_DOC;
  const href = isForm
    ? openUrlFor(route.targetMimeType, route.targetId)
    : `/d/${encodeURIComponent(route.targetId)}`;
  const isActive = !isForm && (activeDriveId === route.targetId || pathname === href);
  const isMoving = useIsMovingFile(node.id);

  const queryClient = useQueryClient();
  const prefetchFile = (): void => {
    void queryClient.prefetchQuery({
      queryKey: wikiKeys.file(route.targetId),
      queryFn: () => wikiApi.getFile(route.targetId),
    });
  };

  const setPrewarm = useDocViewerStore((s) => s.setPrewarm);
  const schedulePrewarm = useDebouncedCallback(() => {
    if (isForm || route.inaccessible) return;
    setPrewarm({
      fileId: route.targetId,
      mimeType: route.isShortcut ? route.targetMimeType : node.mimeType,
      title: node.name,
      reloadKey: 0,
    });
  }, 120);

  const setDocIcon = useSetDocIcon();
  const handleIconChange = async (next: string | null) => {
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

  const iconLeft = `${rowIndent(depth)}px`;
  const linkPadding = `${rowIndent(depth) + ICON_COLUMN_WIDTH}px`;
  const edited = node.modifiedTime
    ? ` — edited ${new Date(node.modifiedTime).toLocaleDateString()}`
    : "";
  const titleAttr =
    route.tooltip ?? (isForm ? `${node.name} — opens in a new tab` : `${node.name}${edited}`);

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
        onPrewarm={schedulePrewarm}
        onPrewarmCancel={schedulePrewarm.cancel}
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
          pickable={isDoc}
          value={iconOverride}
          onChange={handleIconChange}
          ariaLabel="Change icon"
        >
          {iconContent}
        </PickableIcon>
      </div>

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
  onPrewarm,
  onPrewarmCancel,
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
        href={`/d/${encodeURIComponent(route.targetId)}`}
        draggable={false}
        title={titleAttr}
        onPointerDown={onPrefetch}
        onPointerEnter={onPrewarm}
        onPointerLeave={onPrewarmCancel}
      >
        <span className="truncate">{node.name}</span>
      </Link>
    </SidebarMenuButton>
  );
}

export const FileRow = memo(FileRowImpl);
