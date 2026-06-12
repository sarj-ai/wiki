"use client";

import { AlertCircle, ShieldX, ServerCrash } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

import { SarjLogo } from "@/components/sarj-logo";
import { Button } from "@/components/ui/button";
import { getLoginUrl } from "@/lib/auth";
import { REDIRECT_KEY, sanitizeRedirectPath } from "@/lib/url-utils";

const ERROR_CONFIG: Record<
  string,
  { icon: typeof AlertCircle; title: string; message: string; color: string }
> = {
  domain_restricted: {
    icon: ShieldX,
    title: "Access restricted",
    message:
      "Only @sarj.ai accounts can access Sarj Wiki. Please sign in with your Sarj work account.",
    color: "text-warning",
  },
  auth_failed: {
    icon: AlertCircle,
    title: "Authentication failed",
    message:
      "The login session expired or was invalid. This can happen if you waited too long. Please try again.",
    color: "text-destructive",
  },
  server_error: {
    icon: ServerCrash,
    title: "Something went wrong",
    message:
      "We ran into an issue on our end. Please try again. If this keeps happening, contact your admin.",
    color: "text-destructive",
  },
};

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="bg-background text-foreground flex min-h-screen flex-col items-center justify-center">
          <SarjLogo size={120} priority />
        </div>
      }
    >
      <LoginContent />
    </Suspense>
  );
}

function LoginContent() {
  const searchParams = useSearchParams();
  const errorType = searchParams.get("error");
  const errorConfig = errorType ? ERROR_CONFIG[errorType] : null;

  // The middleware gate redirects here with ?redirect=<original path>.
  // The OAuth round-trip through Google won't carry the param, so stash
  // it in localStorage; RedirectAfterLogin consumes it when the
  // callback lands the user back on "/".
  const redirect = searchParams.get("redirect");
  useEffect(() => {
    if (redirect) {
      localStorage.setItem(REDIRECT_KEY, sanitizeRedirectPath(redirect));
    }
  }, [redirect]);

  return (
    <div className="bg-background text-foreground flex min-h-screen flex-col items-center justify-center px-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-8">
        {/* Logo / Title */}
        <div className="flex flex-col items-center gap-3">
          <SarjLogo size={120} priority />
          <p className="text-muted-foreground text-sm">Internal knowledge base for Sarj</p>
        </div>

        {/* Error card */}
        {errorConfig &&
          (() => {
            const Icon = errorConfig.icon;
            return (
              <div className="border-border bg-card w-full rounded-xl border p-5">
                <div className="flex gap-3">
                  <Icon className={`mt-0.5 size-5 shrink-0 ${errorConfig.color}`} />
                  <div className="flex flex-col gap-1.5">
                    <p className="text-card-foreground text-sm font-semibold">
                      {errorConfig.title}
                    </p>
                    <p className="text-muted-foreground text-sm leading-relaxed text-pretty">
                      {errorConfig.message}
                    </p>
                  </div>
                </div>
              </div>
            );
          })()}

        {/* Sign in button */}
        <Button asChild size="lg" className="w-full gap-3 rounded-lg py-3">
          <a href={getLoginUrl()}>
            <svg className="size-5" viewBox="0 0 24 24">
              <path
                fill="currentColor"
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
              />
              <path
                fill="currentColor"
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              />
              <path
                fill="currentColor"
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
              />
              <path
                fill="currentColor"
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
              />
            </svg>
            {errorType ? "Try again with Google" : "Sign in with Google"}
          </a>
        </Button>
      </div>
    </div>
  );
}
