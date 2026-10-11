"use client";

import { useEffect } from "react";

import { resetExpansionState } from "@/hooks/use-expanded-folders";
import {
  clearUserBrowserStorage,
  purgeLegacyBrowserStorage,
  readActiveBrowserUser,
  writeActiveBrowserUser,
} from "@/lib/browser-storage";
import { BrowserUserContext } from "@/lib/browser-user-context";
import {
  broadcastSessionClear,
  resetDocumentViewer,
  resetSidebarState,
} from "@/lib/session-isolation";

export function BrowserSessionBoundary({
  children,
  initialUserId,
}: {
  children: React.ReactNode;
  initialUserId: null | string;
}) {
  const currentUserId = initialUserId;

  useEffect(() => {
    purgeLegacyBrowserStorage();
    if (!currentUserId) return;
    const previousUserId = readActiveBrowserUser();
    if (previousUserId && previousUserId !== currentUserId) {
      resetExpansionState(previousUserId);
      clearUserBrowserStorage(previousUserId);
      resetDocumentViewer();
      resetSidebarState();
      broadcastSessionClear();
    }
    writeActiveBrowserUser(currentUserId);
  }, [currentUserId]);

  return <BrowserUserContext value={currentUserId}>{children}</BrowserUserContext>;
}
