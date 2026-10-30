"use client";

import dynamic from "next/dynamic";

export const LazyDocumentPolishDialog = dynamic(
  async () => {
    const loadedModule = await import("./document-polish-dialog");
    return loadedModule.DocumentPolishDialog;
  },
  { ssr: false },
);
