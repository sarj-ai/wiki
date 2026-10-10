import type { Bootstrap, UserProfile } from "@/lib/wiki-api";

interface BootstrapApi {
  getBootstrap: (signal?: AbortSignal) => Promise<Bootstrap>;
  getMe: (signal?: AbortSignal) => Promise<UserProfile>;
}

interface SessionBootstrap {
  bootstrap: Bootstrap | null;
  me: null | UserProfile;
}

export async function loadSessionBootstrap(api: BootstrapApi): Promise<SessionBootstrap> {
  try {
    const bootstrap = await api.getBootstrap(AbortSignal.timeout(1_500));
    return { bootstrap, me: bootstrap.me };
  } catch {
    // Drive preload failures must not strand an authenticated session in an
    // anonymous persistence namespace. Authenticate again using the same
    // server request credentials, never a profile cached in the browser.
    const me = await api.getMe(AbortSignal.timeout(1_500)).catch(() => null);
    return { bootstrap: null, me };
  }
}
