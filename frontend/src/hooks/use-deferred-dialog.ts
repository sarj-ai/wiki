"use client";

import { useState } from "react";

// Keep a dialog's async work and recovery state alive after its first use.
export function useDeferredDialog() {
  const [state, setState] = useState({ mounted: false, open: false });
  const onOpenChange = (open: boolean) => {
    setState((previous) =>
      previous.open === open ? previous : { mounted: previous.mounted || open, open },
    );
  };
  return { ...state, onOpenChange };
}
