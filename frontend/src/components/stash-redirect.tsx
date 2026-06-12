"use client";

import { useEffect } from "react";

import { REDIRECT_KEY, sanitizeRedirectPath } from "@/lib/url-utils";

export function StashRedirect({ redirect }: { redirect: string | undefined }) {
  useEffect(() => {
    if (redirect) {
      localStorage.setItem(REDIRECT_KEY, sanitizeRedirectPath(redirect));
    }
  }, [redirect]);
  return null;
}
