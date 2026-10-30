import { AlertCircle, ServerCrash, ShieldX } from "lucide-react";

import { SarjLogo } from "@/components/sarj-logo";
import { StashRedirect } from "@/components/stash-redirect";
import { Button } from "@/components/ui/button";
import { getLoginUrl } from "@/lib/auth";
import { AuthErrorParamSchema } from "@/lib/storage-schemas";

const ERROR_CONFIG: Record<
  string,
  { icon: typeof AlertCircle; title: string; message: string; color: string }
> = {
  auth_failed: {
    color: "text-destructive",
    icon: AlertCircle,
    message:
      "The login session expired or was invalid. This can happen if you waited too long. Please try again.",
    title: "Authentication failed",
  },
  domain_restricted: {
    color: "text-warning",
    icon: ShieldX,
    message:
      "This Google account is not authorized for this workspace. Sign in with your work account.",
    title: "Access restricted",
  },
  server_error: {
    color: "text-destructive",
    icon: ServerCrash,
    message:
      "We ran into an issue on our end. Please try again. If this keeps happening, contact your admin.",
    title: "Something went wrong",
  },
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const errorType = AuthErrorParamSchema.safeParse(params["error"]).data;
  const redirect = typeof params["redirect"] === "string" ? params["redirect"] : undefined;
  const errorConfig = errorType ? ERROR_CONFIG[errorType] : undefined;

  return (
    <main className="bg-background text-foreground flex min-h-svh flex-col items-center justify-center px-4 py-8">
      <h1 className="sr-only">Sign in to Sarj Wiki</h1>
      <StashRedirect redirect={redirect} />
      <div className="flex w-full max-w-sm flex-col items-center gap-8">
        <div className="flex flex-col items-center gap-3">
          <SarjLogo size={120} />
          <p className="text-muted-foreground text-sm">Internal knowledge base for Sarj</p>
        </div>

        {errorConfig ? (
          <div className="border-border bg-card w-full rounded-xl border p-5" role="alert">
            <div className="flex gap-3">
              <errorConfig.icon
                aria-hidden="true"
                className={`mt-0.5 size-5 shrink-0 ${errorConfig.color}`}
              />
              <div className="flex flex-col gap-1.5">
                <p className="text-card-foreground text-sm font-semibold">{errorConfig.title}</p>
                <p className="text-muted-foreground text-sm leading-relaxed text-pretty">
                  {errorConfig.message}
                </p>
              </div>
            </div>
          </div>
        ) : null}

        <Button asChild className="w-full gap-3 rounded-lg py-3" size="lg">
          <a href={getLoginUrl()}>
            <svg aria-hidden="true" className="size-5" viewBox="0 0 24 24">
              <path
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
                fill="currentColor"
              />
              <path
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                fill="currentColor"
              />
              <path
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                fill="currentColor"
              />
              <path
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                fill="currentColor"
              />
            </svg>
            {errorType ? "Try again with Google" : "Sign in with Google"}
          </a>
        </Button>
      </div>
    </main>
  );
}
