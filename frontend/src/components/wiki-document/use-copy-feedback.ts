"use client";

import { useEffect, useRef, useState } from "react";

import { SHORT_LINK_COPY_FEEDBACK_MS } from "@/lib/document-short-link";

export function useCopyFeedback(identity: string) {
  const [identityState, setIdentityState] = useState(() => ({ identity, owner: Symbol(identity) }));
  if (identityState.identity !== identity) {
    setIdentityState({ identity, owner: Symbol(identity) });
  }
  const owner = identityState.owner;
  const [copiedOwner, setCopiedOwner] = useState<null | symbol>(null);
  const currentOwner = useRef<null | symbol>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    currentOwner.current = owner;
    return () => {
      currentOwner.current = null;
      window.clearTimeout(timer.current);
    };
  }, [owner]);

  const showCopied = () => {
    if (currentOwner.current !== owner) return;
    window.clearTimeout(timer.current);
    setCopiedOwner(owner);
    timer.current = window.setTimeout(() => {
      timer.current = undefined;
      setCopiedOwner(null);
    }, SHORT_LINK_COPY_FEEDBACK_MS);
  };

  return { copied: copiedOwner === owner, showCopied };
}
