import { MIME_FOLDER, MIME_SHORTCUT } from "@/types/wiki";

export interface ResolvedShortcut {
  inaccessible: boolean;
  isFolderShortcut: boolean;
  isShortcut: boolean;
  targetId: string;
  targetMimeType: string;
  tooltip: null | string;
}

interface ShortcutFields {
  id: string;
  mimeType: string;
  shortcutInaccessible: boolean;
  shortcutTargetDriveName?: string | undefined;
  shortcutTargetId?: string | undefined;
  shortcutTargetMimeType?: string | undefined;
}

export const SHORTCUT_INACCESSIBLE_TOOLTIP =
  "You don't have access to this shortcut's target. Contact your admin.";

export function resolveShortcutTarget(node: ShortcutFields): ResolvedShortcut {
  if (node.mimeType !== MIME_SHORTCUT) {
    return {
      inaccessible: false,
      isFolderShortcut: false,
      isShortcut: false,
      targetId: node.id,
      targetMimeType: node.mimeType,
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
    inaccessible,
    isFolderShortcut: node.shortcutTargetMimeType === MIME_FOLDER,
    isShortcut: true,
    targetId: node.shortcutTargetId ?? node.id,
    targetMimeType: node.shortcutTargetMimeType ?? "",
    tooltip,
  };
}
