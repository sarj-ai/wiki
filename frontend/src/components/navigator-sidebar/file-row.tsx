"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { renderMimeIcon } from "@/lib/file-icons";
import type { DriveNode } from "@/types/navigator";
import { ItemMenu } from "./item-menu";

interface FileRowProps {
  node: DriveNode;
  driveId: string | null;
  parentFolderId: string | null;
  activeDriveId: string | null;
  iconOverride: string | null;
}

export function FileRow({
  node,
  driveId,
  parentFolderId,
  activeDriveId,
  iconOverride,
}: FileRowProps) {
  const pathname = usePathname();
  const href = `/d/${encodeURIComponent(node.id)}`;
  const isActive = activeDriveId === node.id || pathname === href;

  return (
    <>
      <SidebarMenuButton asChild isActive={isActive} className="pr-8">
        <Link href={href}>
          {iconOverride ? (
            <span className="text-base leading-none">{iconOverride}</span>
          ) : (
            renderMimeIcon(node.mimeType, "size-4 shrink-0")
          )}
          <span className="truncate">{node.name}</span>
        </Link>
      </SidebarMenuButton>
      <div className="absolute top-1 right-1 flex items-center opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100">
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
