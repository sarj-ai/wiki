"use client";

import { useState } from "react";

export function useDeferredDialog() {
  const [state, setState] = useState({ mounted: false, open: false });
  const onOpenChange = (open: boolean) => {
    setState((previous) =>
      previous.open === open
        ? previous
        : { mounted: previous.mounted || open, open },
    );
  };
  return { ...state, onOpenChange };
}
