"use client";

import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";

type FocusTargetRef = React.RefObject<HTMLElement | null>;
type AutoFocusHandler = (event: Event) => void;

interface FocusOpening {
  claim: ReturnType<ReturnType<typeof createFocusOwner>["capture"]>;
  content: HTMLElement | null;
  fallbackTargetRef: FocusTargetRef | null;
  fallbackTargetRefs: readonly FocusTargetRef[] | undefined;
  target: HTMLElement | null;
  targetRef: FocusTargetRef | null;
}

const FocusDepthContext = createContext(0);

const FocusReturnContext = createContext<{
  fallbackTargetRef: FocusTargetRef | null;
  fallbackTargetRefs: readonly FocusTargetRef[] | undefined;
  owner: ReturnType<typeof createFocusOwner>;
  targetRef: FocusTargetRef;
} | null>(null);

export function FocusReturnProvider({
  children,
  fallbackTargetRef,
  fallbackTargetRefs,
  targetRef,
}: {
  children: React.ReactNode;
  fallbackTargetRef?: FocusTargetRef;
  fallbackTargetRefs?: readonly FocusTargetRef[];
  targetRef: FocusTargetRef;
}) {
  const [owner] = useState(createFocusOwner);
  useLayoutEffect(() => {
    owner.activate(targetRef);
    return () => owner.retire();
  }, [fallbackTargetRef, fallbackTargetRefs, owner, targetRef]);
  return (
    <FocusReturnContext
      value={{ fallbackTargetRef: fallbackTargetRef ?? null, fallbackTargetRefs, owner, targetRef }}
    >
      <FocusDepthContext value={0}>{children}</FocusDepthContext>
    </FocusReturnContext>
  );
}

export function FocusReturnScope({
  children,
  depth,
}: {
  children: React.ReactNode;
  depth: number;
}) {
  return <FocusDepthContext value={depth}>{children}</FocusDepthContext>;
}

export function useFocusReturn({
  onCloseAutoFocus,
  onOpenAutoFocus,
}: {
  onCloseAutoFocus?: AutoFocusHandler | undefined;
  onOpenAutoFocus?: AutoFocusHandler | undefined;
}) {
  const explicit = useContext(FocusReturnContext);
  const depth = useContext(FocusDepthContext);
  const [localOwner] = useState(createFocusOwner);
  const opening = useRef<FocusOpening | null>(null);
  useLayoutEffect(() => {
    // A conditionally mounted controlled dialog still receives Radix's
    // deferred close callback after Content unmounts. Its connected opener
    // owns that normal return; an explicit provider owns retirement instead.
    localOwner.activate(null);
  }, [localOwner]);
  useEffect(() => {
    const lease = opening.current;
    if (lease?.content?.isConnected && lease.content.dataset["state"] === "open") {
      lease.claim.discard();
      opening.current = {
        ...lease,
        claim: (explicit?.owner ?? localOwner).capture(explicit?.targetRef ?? null, depth),
      };
    }
  }, [depth, explicit?.owner, explicit?.targetRef, localOwner]);

  return {
    depth: depth + 1,
    onOpenAutoFocus: (event: Event) => {
      const ownerTargetRef = explicit?.targetRef ?? null;
      const content = event.target instanceof HTMLElement ? event.target : null;
      opening.current?.claim.discard();
      opening.current = {
        claim: (explicit?.owner ?? localOwner).capture(ownerTargetRef, depth),
        content,
        fallbackTargetRef: explicit?.fallbackTargetRef ?? null,
        fallbackTargetRefs: explicit?.fallbackTargetRefs,
        ...captureReturnTarget(content, ownerTargetRef),
      };
      onOpenAutoFocus?.(event);
    },
    onCloseAutoFocus: (event: Event) => {
      const lease = opening.current;
      onCloseAutoFocus?.(event);
      if (!lease) return;
      if (event.target !== lease.content) {
        if (!event.defaultPrevented) event.preventDefault();
        return;
      }
      if (lease.content?.isConnected && lease.content.dataset["state"] === "open") {
        if (!event.defaultPrevented) event.preventDefault();
        return;
      }
      if (event.defaultPrevented) {
        lease.claim.discard();
        if (opening.current === lease) opening.current = null;
        return;
      }
      if (!lease.claim.isCurrent()) {
        lease.claim.discard();
        if (opening.current === lease) opening.current = null;
        event.preventDefault();
        return;
      }
      const canRestore = lease.claim.release();
      if (!canRestore) return;
      // Radix first restores a standard trigger. Supply a fallback only when
      // a controlled overlay has no surviving trigger or consumer focus.
      queueMicrotask(() => {
        if (opening.current !== lease) return;
        opening.current = null;
        if (!canRestore()) return;
        const target = resolveReturnTarget(lease);
        if (!target) return;
        const active = document.activeElement;
        if (
          active !== null &&
          active !== document.body &&
          active.isConnected &&
          !lease.content?.contains(active)
        )
          return;
        target.focus({ preventScroll: true });
      });
    },
  };
}

function captureReturnTarget(content: HTMLElement | null, targetRef: FocusTargetRef | null) {
  const previous = document.activeElement;
  const parentDialog =
    previous instanceof HTMLElement
      ? previous.closest('[role="dialog"], [role="alertdialog"]')
      : null;
  // A sibling controlled dialog can open from a parent dialog without a
  // native trigger. Its return target belongs to that parent scope.
  if (
    parentDialog &&
    parentDialog !== content &&
    !parentDialog.contains(targetRef?.current ?? null)
  )
    return { target: previous instanceof HTMLElement ? previous : null, targetRef: null };
  const candidate = targetRef?.current ?? previous;
  return { target: candidate instanceof HTMLElement ? candidate : null, targetRef };
}

function resolveReturnTarget({
  fallbackTargetRef,
  fallbackTargetRefs,
  target,
  targetRef,
}: FocusOpening) {
  if (target && (!targetRef || targetRef.current === target) && canReturnFocus(target))
    return target;
  const fallback = fallbackTargetRef?.current;
  if (fallback && canReturnFocus(fallback)) return fallback;
  if (fallbackTargetRefs != null) {
    for (const candidateRef of fallbackTargetRefs) {
      const candidate = candidateRef.current;
      if (candidate && canReturnFocus(candidate)) return candidate;
    }
  }
  return null;
}

function createFocusOwner() {
  let active = false;
  let lifetime = 0;
  let epoch = 0;
  let currentRef: FocusTargetRef | null = null;
  let claims: {
    depth: number;
    epoch: number;
    lifetime: number;
    targetRef: FocusTargetRef | null;
  }[] = [];
  const top = () =>
    claims.reduce<(typeof claims)[number] | null>(
      (current, claim) => (current && current.depth > claim.depth ? current : claim),
      null,
    );
  return {
    activate: (targetRef: FocusTargetRef | null) => {
      active = true;
      lifetime += 1;
      epoch += 1;
      claims = [];
      currentRef = targetRef;
    },
    capture: (targetRef: FocusTargetRef | null, depth: number) => {
      const claim = { depth, epoch: ++epoch, lifetime, targetRef };
      claims.push(claim);
      const isCurrent = () =>
        active &&
        claim.lifetime === lifetime &&
        currentRef === targetRef &&
        top()?.epoch === claim.epoch;
      const discard = () => {
        if (!claims.includes(claim)) return;
        claims = claims.filter((candidate) => candidate !== claim);
        epoch += 1;
      };
      return {
        discard,
        isCurrent,
        release: () => {
          const current = isCurrent();
          discard();
          const closedEpoch = epoch;
          return current
            ? () =>
                active &&
                claim.lifetime === lifetime &&
                currentRef === targetRef &&
                epoch === closedEpoch
            : null;
        },
      };
    },
    retire: () => {
      active = false;
      lifetime += 1;
      epoch += 1;
      claims = [];
    },
  };
}

function canReturnFocus(element: HTMLElement): boolean {
  if (
    !element.isConnected ||
    element === document.body ||
    element.ownerDocument !== document ||
    element.matches(':disabled, [aria-disabled="true"]') ||
    element.closest('[hidden], [inert], [aria-hidden="true"]')
  )
    return false;
  const view = element.ownerDocument.defaultView;
  if (!view) return false;
  for (let current: HTMLElement | null = element; current; current = current.parentElement) {
    const style = view.getComputedStyle(current);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.visibility === "collapse"
    )
      return false;
  }
  return element.tabIndex >= 0 || element.hasAttribute("tabindex");
}
