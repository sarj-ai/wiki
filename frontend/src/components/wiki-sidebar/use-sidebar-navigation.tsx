"use client";

import { createContext, useContext, useEffect, useLayoutEffect, useRef } from "react";

import { useSidebar } from "@/components/ui/sidebar";
import { registerCommandPaletteNavigationOwner } from "@/hooks/use-command-palette";

export interface SidebarNavigationIntent {
  onNavigate: (href: string) => void;
  signal: AbortSignal;
}

interface PendingNavigation {
  source: string;
  target: string;
}
interface SidebarView {
  isMobile: boolean;
  openMobile: boolean;
  pathname: string;
}

const SidebarNavigationContext = createContext<{
  capture: () => null | SidebarNavigationIntent;
  navigate: (href: string) => void;
}>({ capture: () => null, navigate: ignoreNavigation });

export function SidebarNavigationProvider({
  children,
  ownerKey,
  pathname,
}: {
  children: React.ReactNode;
  ownerKey: null | string;
  pathname: string;
}) {
  const { isMobile, openMobile, setOpenMobile } = useSidebar();
  const pending = useRef<null | PendingNavigation>(null);
  const lifetime = useRef<AbortController | null>(null);
  const view = useRef<null | SidebarView>(null);
  useLayoutEffect(() => {
    const owner = new AbortController();
    lifetime.current = owner;
    pending.current = null;
    return () => {
      owner.abort();
      pending.current = null;
    };
  }, [ownerKey]);
  useLayoutEffect(() => {
    view.current = { isMobile, openMobile, pathname };
  }, [isMobile, openMobile, pathname]);
  useEffect(() => {
    const navigation = pending.current;
    if (!isMobile || !openMobile) {
      pending.current = null;
      return;
    }
    if (!navigation) return;
    if (pathname === navigation.target) {
      pending.current = null;
      setOpenMobile(false);
    } else if (pathname !== navigation.source) {
      pending.current = null;
    }
  }, [isMobile, openMobile, pathname, setOpenMobile]);
  const capture = () => captureNavigation(ownerKey, lifetime, pending, view, setOpenMobile);
  const navigate = (href: string) => capture()?.onNavigate(href);
  useLayoutEffect(() => {
    if (!isMobile || !openMobile) return;
    const owner = captureNavigation(ownerKey, lifetime, pending, view, setOpenMobile);
    if (!owner) return;
    return registerCommandPaletteNavigationOwner(owner);
  }, [isMobile, openMobile, ownerKey, setOpenMobile]);
  return (
    <SidebarNavigationContext value={{ capture, navigate }}>{children}</SidebarNavigationContext>
  );
}

function captureNavigation(
  ownerKey: null | string,
  lifetime: React.RefObject<AbortController | null>,
  pending: React.RefObject<null | PendingNavigation>,
  view: React.RefObject<null | SidebarView>,
  setOpenMobile: (open: boolean) => void,
): null | SidebarNavigationIntent {
  const signal = lifetime.current?.signal;
  if (!ownerKey || !signal || signal.aborted) return null;
  return {
    onNavigate: (href) => {
      if (signal.aborted || lifetime.current?.signal !== signal) return;
      const current = view.current;
      if (!current?.isMobile || !current.openMobile) return;
      if (current.pathname === href) {
        pending.current = null;
        setOpenMobile(false);
      } else {
        pending.current = { source: current.pathname, target: href };
      }
    },
    signal,
  };
}

export function useSidebarNavigation() {
  return useContext(SidebarNavigationContext).navigate;
}

export function useSidebarNavigationIntent() {
  return useContext(SidebarNavigationContext).capture;
}

function ignoreNavigation(): void {
  // Rows rendered outside a Wiki sidebar have no mobile navigation owner.
}
