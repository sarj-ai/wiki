"use client";

import { createContext, useContext } from "react";

import type { DriveNode } from "@/types/wiki";

const DriveSectionContext = createContext<DriveSectionValue | null>(null);

export interface DriveSectionValue {
  driveId: string;
  activeDriveId: string | null;
  showNonDocFiles: boolean;
  childrenByParent: Map<string, DriveNode[]>;
  filesByParent: Map<string, DriveNode[]>;
  treeFilesComplete: boolean;
  iconOverrides: Map<string, string | undefined>;
  docIconOverrides: Map<string, string | undefined>;
  sortOrderMap: Map<string, number>;
  optimisticByContainer: Map<string, string[]>;
}

export const DriveSectionProvider = DriveSectionContext.Provider;

export function useDriveSectionContext(): DriveSectionValue {
  const value = useContext(DriveSectionContext);
  if (value === null) {
    throw new Error("useDriveSectionContext must be used inside <DriveSectionProvider>");
  }
  return value;
}
