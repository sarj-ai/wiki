"use client";

import { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import type { User } from "@/types";
import { fetchCurrentUser, logout as apiLogout } from "@/lib/auth";

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
      const fetchedUser = await fetchCurrentUser();
      setUser(fetchedUser);
      if (fetchedUser) {
        // Auth succeeded — check for a saved redirect path
        const redirect = localStorage.getItem(REDIRECT_KEY);
        if (redirect) {
          localStorage.removeItem(REDIRECT_KEY);
          router.push(redirect);
        }
      } else if (pathnameRef.current !== "/login") {
        localStorage.setItem(REDIRECT_KEY, pathnameRef.current);
        window.location.href = `/login?redirect=${encodeURIComponent(pathnameRef.current)}`;
      }
    } catch {
      if (pathnameRef.current !== "/login") {
        localStorage.setItem(REDIRECT_KEY, pathnameRef.current);
        window.location.href = `/login?redirect=${encodeURIComponent(pathnameRef.current)}`;
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
  useEffect(() => {
    function onFocus() {
      checkAuth();
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
    <AuthContext.Provider value={{ user, isLoading, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
