"use client";

import { useEffect } from "react";

import { storeLoginRedirect } from "@/lib/browser-storage";
import { sanitizeRedirectPath } from "@/lib/sanitize-redirect-path";

export function StashRedirect({ redirect }: { redirect: string | undefined }) {
  useEffect(() => {
    if (redirect) {
      storeLoginRedirect(sanitizeRedirectPath(redirect));
    }
  }, [redirect]);
  return null;
}
