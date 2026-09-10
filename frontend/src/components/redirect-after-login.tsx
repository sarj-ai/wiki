"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { takeLoginRedirect } from "@/lib/browser-storage";
import { sanitizeRedirectPath } from "@/lib/sanitize-redirect-path";

export function RedirectAfterLogin() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (pathname !== "/") return;
    const stashed = takeLoginRedirect();
    if (stashed === null) return;
    const safePath = sanitizeRedirectPath(stashed);
    if (safePath !== pathname) {
      router.replace(safePath);
    }
  }, [pathname, router]);

  return null;
}
