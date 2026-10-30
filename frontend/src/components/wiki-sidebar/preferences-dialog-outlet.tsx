"use client";

import { useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { createContext, useContext, useEffect, useRef, useState } from "react";

import { FocusReturnProvider } from "@/components/ui/focus-return";
import { useSidebar } from "@/components/ui/sidebar";
import { captureMutationSession } from "@/lib/mutation-session";

const PreferencesDialog = dynamic(
  async () => {
    const loadedModule = await import("./preferences-dialog");
    return loadedModule.PreferencesDialog;
  },
  { ssr: false },
);

type OpenerRef = React.RefObject<HTMLButtonElement | null>;
interface PreferencesOpening {
  open: boolean;
  opener: OpenerRef;
  session: AbortSignal;
  token: symbol;
}

const PreferencesDialogContext = createContext<{
  hasActiveScope: () => boolean;
  openPreferences: (opener: OpenerRef) => void;
  registerOpener: (element: HTMLButtonElement) => () => void;
} | null>(null);

export function usePreferencesDialogOutlet() {
  const outlet = useContext(PreferencesDialogContext);
  if (!outlet) throw new Error("Preferences requires a PreferencesDialogOutletProvider");
  return outlet;
}

export function PreferencesDialogOutletProvider({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: null | string;
}) {
  return (
    <PreferencesDialogOwner key={userId ?? ""} userId={userId}>
      {children}
    </PreferencesDialogOwner>
  );
}

function PreferencesDialogOwner({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: null | string;
}) {
  const queryClient = useQueryClient();
  const { mobileTriggerRef } = useSidebar();
  const accountTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [fallbackTargetRefs] = useState(() => [accountTriggerRef, mobileTriggerRef]);
  const [opening, setOpening] = useState<null | PreferencesOpening>(null);
  const contentRef = useRef<{ content: HTMLElement | null; token: symbol } | null>(null);
  useEffect(() => {
    if (!opening) return;
    const retire = () =>
      setOpening((current) => (current?.token === opening.token ? null : current));
    if (opening.session.aborted) {
      retire();
      return;
    }
    opening.session.addEventListener("abort", retire);
    return () => opening.session.removeEventListener("abort", retire);
  }, [opening]);

  const currentOpening = opening?.session.aborted ? null : opening;
  return (
    <PreferencesDialogContext
      value={{
        hasActiveScope: () =>
          !!currentOpening &&
          contentRef.current?.token === currentOpening.token &&
          !!contentRef.current.content?.isConnected,
        openPreferences: (opener) => {
          const session = captureMutationSession(queryClient);
          if (!userId || session.aborted) return;
          setOpening({ open: true, opener, session, token: Symbol() });
        },
        registerOpener: (element) => {
          accountTriggerRef.current = element;
          return () => {
            if (accountTriggerRef.current === element) accountTriggerRef.current = null;
          };
        },
      }}
    >
      {children}
      {currentOpening ? (
        <FocusReturnProvider
          fallbackTargetRefs={fallbackTargetRefs}
          targetRef={currentOpening.opener}
        >
          <PreferencesDialog
            onCloseAutoFocus={(event) => {
              if (
                contentRef.current?.token !== currentOpening.token ||
                event.target !== contentRef.current.content ||
                (contentRef.current.content?.isConnected &&
                  contentRef.current.content.dataset["state"] === "open")
              )
                return;
              contentRef.current = null;
              // Keep the dialog's mutation observer and focus owner alive
              // after dismissal, including a close/reopen during a save.
            }}
            onOpenAutoFocus={(event) => {
              contentRef.current = {
                content: event.target instanceof HTMLElement ? event.target : null,
                token: currentOpening.token,
              };
            }}
            onOpenChange={(open) => {
              setOpening((current) =>
                current?.token === currentOpening.token ? { ...current, open } : current,
              );
            }}
            open={currentOpening.open}
          />
        </FocusReturnProvider>
      ) : null}
    </PreferencesDialogContext>
  );
}
