"use client";

import { useCallback, useEffect, useState } from "react";

const EDITOR_PREF_KEY = "sarj-always-iframe";

export function useEditorPreference() {
  const [alwaysIframe, setAlwaysIframe] = useState(false);

  useEffect(() => {
    try {
      setAlwaysIframe(localStorage.getItem(EDITOR_PREF_KEY) === "true");
    } catch {
      // localStorage may be unavailable in private browsing
    }
  }, []);

  const toggle = useCallback(() => {
    setAlwaysIframe((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(EDITOR_PREF_KEY, String(next));
      } catch {
        // localStorage may be unavailable
      }
      return next;
    });
  }, []);

  return { alwaysIframe, toggleAlwaysIframe: toggle };
}
