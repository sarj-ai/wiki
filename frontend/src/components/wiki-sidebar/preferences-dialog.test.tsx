import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import { wikiKeys } from "@/lib/wiki-keys";
import type { Preferences } from "@/types/wiki";

import { PreferencesDialog } from "./preferences-dialog";

const ACTION = vi.hoisted(() => vi.fn<() => Promise<ActionResult<Preferences>>>());
vi.mock("@/app/actions/wiki-actions", () => ({ setPreferencesAction: ACTION }));

it("shows saving feedback for staged preferences without announcing success before confirmation", async () => {
  vi.useFakeTimers();
  const client = new QueryClient({
    defaultOptions: {
      queries: { gcTime: Infinity, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  client.setQueryData(wikiKeys.preferences, { showNonDocFiles: true, hiddenWorkspaceIds: [] });
  client.setQueryData(wikiKeys.drives, []);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  const pending = Promise.withResolvers<ActionResult<Preferences>>();
  ACTION.mockReturnValueOnce(pending.promise);
  try {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <PreferencesDialog onOpenChange={vi.fn()} open />
        </QueryClientProvider>,
      ),
    );
    const checkbox = document.querySelector<HTMLElement>('[role="checkbox"]');
    if (!checkbox) throw new Error("Preference checkbox missing");
    flushSync(() => checkbox.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="status"]')).toHaveTextContent("Saving preferences…");
    expect(checkbox).toHaveAttribute("aria-checked", "false");
    pending.resolve({ ok: true, data: { showNonDocFiles: false, hiddenWorkspaceIds: [] } });
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="status"]')).toBeNull();
  } finally {
    flushSync(() => root.unmount());
    retireMutationSession(client);
    client.clear();
    container.remove();
    vi.useRealTimers();
  }
});
