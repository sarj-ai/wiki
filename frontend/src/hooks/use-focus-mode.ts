"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSidebar } from "@/components/ui/sidebar";

export function useFocusMode() {
  const { open: sidebarOpen, setOpen: setSidebarOpen, toggleSidebar } = useSidebar();
  const [focusMode, setFocusMode] = useState(false);
  const sidebarWasOpen = useRef(true);

  const saveSidebarState = useCallback(() => {
    sidebarWasOpen.current = sidebarOpen;
    if (sidebarOpen) setSidebarOpen(false);
  }, [sidebarOpen, setSidebarOpen]);

  const restoreSidebarState = useCallback(() => {
    if (sidebarWasOpen.current) setSidebarOpen(true);
  }, [setSidebarOpen]);

  const enterFocusMode = useCallback(() => {
    saveSidebarState();
    setFocusMode(true);
  }, [saveSidebarState]);

  const exitFocusMode = useCallback(() => {
    restoreSidebarState();
    setFocusMode(false);
  }, [restoreSidebarState]);

  const toggleFocusMode = useCallback(() => {
    if (focusMode) {
      restoreSidebarState();
    } else {
      saveSidebarState();
    }
    setFocusMode((prev) => !prev);
  }, [focusMode, saveSidebarState, restoreSidebarState]);

  // Keyboard shortcut: Cmd+.
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "." && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggleFocusMode();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [toggleFocusMode]);

  return {
    focusMode,
    sidebarOpen,
    toggleSidebar,
    enterFocusMode,
    exitFocusMode,
    toggleFocusMode,
  };
}
