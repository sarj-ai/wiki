"use client";

import { createContext, useContext } from "react";

export const BrowserUserContext = createContext<null | string>(null);

export function useBrowserUserId(): null | string {
  return useContext(BrowserUserContext);
}
