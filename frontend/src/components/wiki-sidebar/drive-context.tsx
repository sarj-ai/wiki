"use client";

import { createContext, useContext } from "react";

import type { DriveNode } from "@/types/wiki";

const DriveSectionContext = createContext<DriveSectionValue | null>(null);

export interface DriveSectionValue {
  activeDriveId: null | string;
  childrenByParent: Map<string, DriveNode[]>;
  docIconOverrides: Map<string, string | undefined>;
  driveId: string;
  filesByParent: Map<string, DriveNode[]>;
  iconOverrides: Map<string, string | undefined>;
  optimisticByContainer: Map<string, string[]>;
  pathname?: string;
  showNonDocFiles: boolean;
  sortOrderMap: Map<string, number>;
  treeFilesComplete: boolean;
}

export const DriveSectionProvider = DriveSectionContext.Provider;

export function useDriveSectionContext(): DriveSectionValue {
  const value = useContext(DriveSectionContext);
  if (value === null) {
    throw new Error("useDriveSectionContext must be used inside <DriveSectionProvider>");
  }
  return value;
}
