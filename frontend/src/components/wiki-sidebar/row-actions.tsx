import type { ReactNode } from "react";

export function RowActions({ children }: { children: ReactNode }) {
  return (
    <div
      className="absolute top-1/2 right-1 z-20 flex -translate-y-1/2 items-center gap-0.5 opacity-0 group-hover/navitem:opacity-100 focus-within:opacity-100"
      data-sidebar-row-actions="true"
    >
      {children}
    </div>
  );
}
