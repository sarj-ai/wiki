"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useState,
  useSyncExternalStore,
} from "react";

import { captureMutationSession } from "@/lib/mutation-session";
import {
  createRecentPagesScope,
  type RecentPagesScope,
  subscribeRecentPagesInvalidation,
} from "@/lib/recent-pages-query";

const RecentPagesContext = createContext<null | RecentPagesScope>(null);

export function RecentPagesSessionProvider({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: null | string;
}) {
  if (userId === null) return <RecentPagesContext value={null}>{children}</RecentPagesContext>;
  return (
    <OwnedRecentPagesSession key={userId} userId={userId}>
      {children}
    </OwnedRecentPagesSession>
  );
}

function OwnedRecentPagesSession({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: string;
}) {
  const queryClient = useQueryClient();
  const ownerId = useId();
  const [scope] = useState(() => createRecentPagesScope({ queryClient, userId, ownerId }));
  useLayoutEffect(() => {
    scope.activate();
    return () => scope.retire();
  }, [scope]);
  useEffect(() => {
    const session = captureMutationSession(queryClient);
    const retire = () => scope.retire();
    session.addEventListener("abort", retire);
    const unsubscribe = subscribeRecentPagesInvalidation(scope);
    return () => {
      session.removeEventListener("abort", retire);
      unsubscribe();
    };
  }, [queryClient, scope]);
  return <RecentPagesContext value={scope}>{children}</RecentPagesContext>;
}

export function useRecentPagesScope(): null | RecentPagesScope {
  const scope = useContext(RecentPagesContext);
  const generation = useSyncExternalStore(
    scope?.subscribe ?? noSubscription,
    scope?.getSnapshot ?? inactiveSession,
    inactiveSession,
  );
  return generation === null || scope === null ? null : { ...scope, generation };
}

function noSubscription(): () => void {
  return noCleanup;
}

function noCleanup(): void {
  // An unverified session has no recent-page owner to unsubscribe.
}

function inactiveSession(): null {
  return null;
}
