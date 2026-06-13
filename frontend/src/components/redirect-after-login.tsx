"use client";

import { type Route } from "next";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { REDIRECT_KEY, sanitizeRedirectPath } from "@/lib/url-utils";

export function RedirectAfterLogin() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (pathname !== "/") return;
    const stashed = localStorage.getItem(REDIRECT_KEY);
    if (stashed === null) return;
    localStorage.removeItem(REDIRECT_KEY);
    const safePath = sanitizeRedirectPath(stashed);
    if (safePath !== pathname) {
      router.replace(safePath as Route);
    }
  }, [pathname, router]);

  return null;
}
