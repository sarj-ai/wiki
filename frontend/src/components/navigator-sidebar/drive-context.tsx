"use client";

import { createContext, useContext } from "react";

import type { DriveNode } from "@/types/navigator";

/**
 * Shared per-drive render context for the sidebar. ``DriveSection``
 * is the provider; every nested ``TreeNode`` reads from it instead of
 * dragging the same eight props through every recursive level.
 *
 * The context is intentionally keyed to a SINGLE drive — a separate
 * provider is mounted per ``DriveSection`` so context values stay
 * narrow and changes in one drive don't invalidate every other
 * drive's subtree.
 */
export interface DriveSectionValue {
  driveId: string;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  childrenByParent: Map<string, DriveNode[]>;
  iconOverrides: Map<string, string | null>;
  docIconOverrides: Map<string, string | null>;
  sortOrderMap: Map<string, number>;
  optimisticByContainer: Map<string, string[]>;
}

const DriveSectionContext = createContext<DriveSectionValue | null>(null);

export const DriveSectionProvider = DriveSectionContext.Provider;

/**
 * Reads the surrounding ``DriveSectionProvider``. Throws if called
 * outside one — the assumption is that every caller is part of a
 * sidebar render path, and a missing provider is a bug, not a state
 * the UI should silently degrade through.
 */
export function useDriveSectionContext(): DriveSectionValue {
  const value = useContext(DriveSectionContext);
  if (value === null) {
    throw new Error("useDriveSectionContext must be used inside <DriveSectionProvider>");
  }
  return value;
}
