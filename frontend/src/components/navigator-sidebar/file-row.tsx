"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { renderMimeIcon } from "@/lib/file-icons";
import type { DriveNode } from "@/types/navigator";

interface FileRowProps {
  node: DriveNode;
  activeDriveId: string | null;
  iconOverride: string | null;
}

export function FileRow({ node, activeDriveId, iconOverride }: FileRowProps) {
  const pathname = usePathname();
  const href = `/d/${encodeURIComponent(node.id)}`;
  const isActive = activeDriveId === node.id || pathname === href;

  return (
    <SidebarMenuButton asChild isActive={isActive}>
      <Link href={href}>
        {iconOverride ? (
          <span className="text-base leading-none">{iconOverride}</span>
        ) : (
          renderMimeIcon(node.mimeType, "size-4 shrink-0")
        )}
        <span className="truncate">{node.name}</span>
      </Link>
    </SidebarMenuButton>
  );
}
