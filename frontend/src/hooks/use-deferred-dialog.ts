"use client";

import { useState } from "react";

type DeferredDialogState = "unmounted" | "open" | "closed";

export function useDeferredDialog() {
  const [state, setState] = useState<DeferredDialogState>("unmounted");
  const onOpenChange = (open: boolean) => {
    setState((previous) =>
      open ? "open" : previous === "unmounted" ? "unmounted" : "closed",
    );
  };
  return {
    mounted: state !== "unmounted",
    open: state === "open",
    onOpenChange,
  };
}
