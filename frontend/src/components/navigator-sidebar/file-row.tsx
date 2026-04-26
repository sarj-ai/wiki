"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { toast } from "sonner";

import type { DriveNode } from "@/types/navigator";

import { SidebarMenuButton } from "@/components/ui/sidebar";
import { useSetDocIcon } from "@/hooks/use-navigator-mutations";
import { renderMimeIcon } from "@/lib/file-icons";
import { openUrlFor } from "@/lib/file-urls";
import { MIME_DOC, MIME_FORM } from "@/types/navigator";

import { EmojiPickerPopover } from "./emoji-picker-popover";
import { ItemMenu } from "./item-menu";

interface FileRowProps {
  node: DriveNode;
  driveId: string | null;
  parentFolderId: string | null;
  activeDriveId: string | null;
  iconOverride: string | null;
  depth: number;
}

// Width of the absolute-positioned icon column in pixels, including
// the gap between it and the Link text. Used both for the icon's
// ``left`` offset and the Link's compensating ``paddingLeft``.
const ICON_COLUMN_WIDTH = 24;

export function FileRow({
  node,
  driveId,
  parentFolderId,
  activeDriveId,
  iconOverride,
  depth,
}: FileRowProps) {
  const pathname = usePathname();
  // Google Forms' /edit view can't be embedded in a cross-origin iframe
  // — the editor hard-gates with a "You need access" page regardless of
  // which API created the form or which account owns it. Opening Forms
  // in a top-level tab is the only working path.
  const isForm = node.mimeType === MIME_FORM;
  const isDoc = node.mimeType === MIME_DOC;
  const href = isForm ? openUrlFor(node.mimeType, node.id) : `/d/${encodeURIComponent(node.id)}`;
  const isActive = !isForm && (activeDriveId === node.id || pathname === href);

  const setDocIcon = useSetDocIcon();
  const handleIconChange = async (next: string | null) => {
    try {
      await setDocIcon.mutateAsync({ driveId: node.id, icon: next });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to update icon";
      toast.error(message);
    }
  };

  const iconContent = iconOverride ? (
    <span className="text-base leading-none">{iconOverride}</span>
  ) : (
    renderMimeIcon(node.mimeType, "size-4 shrink-0")
  );

  // Icon sits OUTSIDE the <Link> as an absolutely-positioned overlay
  // on top of the SidebarMenuButton. Clicking it never bubbles into
  // an anchor element, so there's no risk of Next.js Link navigating
  // on a supposedly "icon-only" click. The Link's left padding is
  // bumped by ICON_COLUMN_WIDTH so the title text stays readable.
  const iconLeft = `${depth * 12 + 8}px`;
  const linkPadding = `${depth * 12 + 8 + ICON_COLUMN_WIDTH}px`;

  const iconElement = isDoc ? (
    <EmojiPickerPopover value={iconOverride} onChange={handleIconChange}>
      <button
        type="button"
        aria-label="Change icon"
        // Icon hover uses the full sidebar highlight color
        // (``--sidebar-accent`` / ``--sidebar-accent-foreground``),
        // the same pair that marks a selected row. Both theme
        // variants are wired through those tokens so light and dark
        // flip automatically.
        className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-md transition-colors"
      >
        {iconContent}
      </button>
    </EmojiPickerPopover>
  ) : (
    <span className="flex size-6 shrink-0 items-center justify-center">{iconContent}</span>
  );

  return (
    <>
      <SidebarMenuButton
        asChild
        isActive={isActive}
        className="pr-8"
        style={{ paddingLeft: linkPadding }}
      >
        <Link
          href={href}
          // HTML5 native anchor drag is on by default. Chrome treats
          // drag-and-drop of an anchor back onto the same page as a
          // navigation to ``href`` — bypassing click handlers entirely.
          // That was the path through which dnd-kit reorders landed the
          // user on the dragged file's URL. Disabling native drag lets
          // dnd-kit's PointerSensor own the drag lifecycle cleanly.
          draggable={false}
          title={isForm ? `${node.name} — opens in a new tab` : node.name}
          target={isForm ? "_blank" : undefined}
          rel={isForm ? "noopener noreferrer" : undefined}
        >
          <span className="truncate">{node.name}</span>
          {isForm && (
            <ExternalLink
              className="text-muted-foreground ml-auto size-3 shrink-0"
              aria-label="Opens in a new tab"
            />
          )}
        </Link>
      </SidebarMenuButton>
      <div className="absolute top-1/2 z-10 -translate-y-1/2" style={{ left: iconLeft }}>
        {iconElement}
      </div>
      <div className="absolute top-1/2 right-1 flex -translate-y-1/2 items-center opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
        <ItemMenu
          fileId={node.id}
          driveId={driveId}
          parentFolderId={parentFolderId}
          currentName={node.name}
          isFolder={false}
        />
      </div>
    </>
  );
}
