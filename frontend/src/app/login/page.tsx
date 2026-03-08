"use client";

import { getLoginUrl } from "@/lib/auth";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-background text-foreground">
      <h1 className="text-4xl font-bold tracking-tight">Sarj Docs</h1>
      <p className="text-muted-foreground">Internal knowledge base for Sarj</p>
      <a
        href={getLoginUrl()}
        className="rounded-lg bg-primary px-6 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
      >
        Sign in with Google
      </a>
    </div>
  );
}
