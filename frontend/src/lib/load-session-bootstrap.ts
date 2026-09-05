import type { Bootstrap, UserProfile } from "@/lib/wiki-api";

interface BootstrapApi {
  getBootstrap: () => Promise<Bootstrap>;
  getMe: () => Promise<UserProfile>;
}

interface SessionBootstrap {
  bootstrap: Bootstrap | null;
  me: UserProfile | null;
}

export async function loadSessionBootstrap(api: BootstrapApi): Promise<SessionBootstrap> {
  try {
    const bootstrap = await api.getBootstrap();
    return { bootstrap, me: bootstrap.me };
  } catch {
    // Drive preload failures must not strand an authenticated session in an
    // anonymous persistence namespace. Authenticate again using the same
    // server request credentials, never a profile cached in the browser.
    const me = await api.getMe().catch(() => null);
    return { bootstrap: null, me };
  }
}
