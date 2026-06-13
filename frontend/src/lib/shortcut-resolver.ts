import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

interface ShortcutFields {
  mimeType: string;
  id: string;
  shortcutInaccessible: boolean;
  shortcutTargetId?: string | undefined;
  shortcutTargetMimeType?: string | undefined;
  shortcutTargetDriveName?: string | undefined;
}

export interface ResolvedShortcut {
  isShortcut: boolean;
  isFolderShortcut: boolean;
  inaccessible: boolean;
  targetMimeType: string;
  targetId: string;
  tooltip: string | null;
}

export const SHORTCUT_INACCESSIBLE_TOOLTIP =
  "You don't have access to this shortcut's target. Contact your admin.";

export function resolveShortcutTarget(node: ShortcutFields): ResolvedShortcut {
  if (node.mimeType !== MIME_SHORTCUT) {
    return {
      isShortcut: false,
      isFolderShortcut: false,
      inaccessible: false,
      targetMimeType: node.mimeType,
      targetId: node.id,
      tooltip: null,
    };
  }
  const inaccessible = node.shortcutInaccessible;
  const tooltip = inaccessible
    ? SHORTCUT_INACCESSIBLE_TOOLTIP
    : node.shortcutTargetDriveName
      ? `Shortcut → ${node.shortcutTargetDriveName}`
      : "Shortcut";
  return {
    isShortcut: true,
    isFolderShortcut: node.shortcutTargetMimeType === MIME_FOLDER,
    inaccessible,
    targetMimeType: node.shortcutTargetMimeType ?? "",
    targetId: node.shortcutTargetId ?? node.id,
    tooltip,
  };
}
