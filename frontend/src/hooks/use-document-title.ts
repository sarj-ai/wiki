"use client";

import { useEffect } from "react";

export const DEFAULT_DOCUMENT_TITLE = "Sarj Wiki";

export function documentTitleFor(name: string): string {
  return `${name} · ${DEFAULT_DOCUMENT_TITLE}`;
}

export function applyDocumentTitle(name: string): () => void {
  document.title = documentTitleFor(name);
  return () => {
    document.title = DEFAULT_DOCUMENT_TITLE;
  };
}

export function useDocumentTitle(name: string): void {
  useEffect(() => applyDocumentTitle(name), [name]);
}
