"use client";

import { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { fetchCurrentUser, refreshAccessToken, logout as apiLogout, type User } from "@/lib/auth";
import { sanitizeRedirectPath } from "@/lib/url-utils";

const REDIRECT_KEY = "sarj-redirect";

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  logout: async () => {},
});

export function useAuth(): AuthContextType {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  const checkAuth = useCallback(async () => {
    try {
      let fetchedUser = await fetchCurrentUser();

      // If auth failed, try refreshing the token before redirecting to login.
      // This prevents a flash of the login page when the access token expired
      // but the refresh token is still valid.
      if (!fetchedUser) {
        const refreshed = await refreshAccessToken();
        if (refreshed) {
          fetchedUser = await fetchCurrentUser();
        }
      }

      setUser(fetchedUser);
      if (fetchedUser) {
        const redirect = localStorage.getItem(REDIRECT_KEY);
        if (redirect) {
          localStorage.removeItem(REDIRECT_KEY);
          router.push(sanitizeRedirectPath(redirect));
        }
      } else if (pathnameRef.current !== "/login") {
        const safePath = sanitizeRedirectPath(pathnameRef.current);
        localStorage.setItem(REDIRECT_KEY, safePath);
        window.location.href = `/login?redirect=${encodeURIComponent(safePath)}`;
      }
    } catch {
      if (pathnameRef.current !== "/login") {
        const safePath = sanitizeRedirectPath(pathnameRef.current);
        localStorage.setItem(REDIRECT_KEY, safePath);
        window.location.href = `/login?redirect=${encodeURIComponent(safePath)}`;
      }
    } finally {
      setIsLoading(false);
    }
  }, [router]);

  useEffect(() => {
    checkAuth();
    // Run once on mount only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-check auth when user returns to the tab (catches expired tokens)
  // Cooldown prevents flooding the API on rapid alt-tabbing
  const lastAuthCheckRef = useRef(0);
  useEffect(() => {
    function onFocus() {
      const now = Date.now();
      if (now - lastAuthCheckRef.current > 30_000) {
        lastAuthCheckRef.current = now;
        checkAuth();
      }
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [checkAuth]);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // Proceed with logout even if the API call fails
    }
    setUser(null);
    router.push("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ user, isLoading, logout }}>{children}</AuthContext.Provider>
  );
}
