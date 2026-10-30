import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import type { ActionResult } from "@/lib/action-result";
import { retireMutationSession } from "@/lib/mutation-session";
import type { PreferencesInput } from "@/lib/wiki-api";
import { wikiKeys } from "@/lib/wiki-keys";
import type { DriveSummary, Preferences } from "@/types/wiki";

import { PreferencesDialog } from "./preferences-dialog";

const READ = vi.hoisted(() => ({
  drives: vi.fn<() => Promise<DriveSummary[]>>(),
  preferences: vi.fn<() => Promise<Preferences>>(),
}));
const WRITE = vi.hoisted(() =>
  vi.fn<(input: PreferencesInput) => Promise<ActionResult<Preferences>>>(),
);
vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getDrives: READ.drives, getPreferences: READ.preferences },
}));
vi.mock("@/app/actions/wiki-actions", () => ({ setPreferencesAction: WRITE }));

it("blocks cold preference edits through read failure, then preserves saved hidden drives after retry", async () => {
  vi.useFakeTimers();
  const client = new QueryClient({
    defaultOptions: {
      mutations: { gcTime: Infinity, retry: false },
      queries: { gcTime: Infinity, retry: false, staleTime: Infinity },
    },
  });
  const cold = Promise.withResolvers<Preferences>();
  const retry = Promise.withResolvers<Preferences>();
  READ.drives.mockResolvedValue([
    { id: "visible-drive", isPersonal: false, name: "Visible workspace" },
    { id: "saved-hidden-drive", isPersonal: false, name: "Previously hidden workspace" },
  ]);
  READ.preferences.mockReturnValueOnce(cold.promise).mockReturnValueOnce(retry.promise);
  const saved = { hiddenWorkspaceIds: ["saved-hidden-drive"], showNonDocFiles: false };
  const updated = { ...saved, hiddenWorkspaceIds: [...saved.hiddenWorkspaceIds, "visible-drive"] };
  READ.preferences.mockResolvedValue(updated);
  WRITE.mockResolvedValue({ ok: true, data: updated });
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    flushSync(() =>
      root.render(
        <QueryClientProvider client={client}>
          <PreferencesDialog onOpenChange={vi.fn()} open />
        </QueryClientProvider>,
      ),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(READ.drives).toHaveBeenCalledOnce();
    expect(READ.preferences).toHaveBeenCalledOnce();
    expect(document.body).toHaveTextContent("Previously hidden workspace");
    expectControlsBlocked();
    expect(client.getQueryData(wikiKeys.preferences)).toBeUndefined();

    cold.reject(new Error("Preferences unavailable"));
    await vi.advanceTimersByTimeAsync(0);
    expect(document.querySelector('[role="alert"]')).toHaveTextContent(/preferences/i);
    expectControlsBlocked();
    const retryButton = [...document.querySelectorAll("button")].find(
      (button) => button.textContent === "Retry preferences",
    );
    if (!retryButton) throw new Error("Preference retry is missing");
    flushSync(() => retryButton.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(READ.preferences).toHaveBeenCalledTimes(2);
    expectControlsBlocked();

    retry.resolve(saved);
    await vi.advanceTimersByTimeAsync(0);
    const display = checkbox("pref-show-non-doc");
    const visible = checkbox("pref-drive-visible-drive");
    expect(display).not.toBeDisabled();
    expect(display).toHaveAttribute("aria-checked", "false");
    expect(visible).not.toBeDisabled();
    expect(checkbox("pref-drive-saved-hidden-drive")).toHaveAttribute("aria-checked", "false");
    flushSync(() => visible.click());
    await vi.advanceTimersByTimeAsync(0);
    expect(WRITE).toHaveBeenCalledExactlyOnceWith({
      hiddenWorkspaceIds: ["saved-hidden-drive", "visible-drive"],
    });
    expect(client.getQueryData(wikiKeys.preferences)).toEqual(updated);
  } finally {
    flushSync(() => root.unmount());
    retireMutationSession(client);
    client.clear();
    container.remove();
    vi.useRealTimers();
  }
});

function expectControlsBlocked() {
  for (const id of ["pref-show-non-doc", "pref-drive-visible-drive"]) {
    const control = checkbox(id);
    expect(control).toBeDisabled();
    flushSync(() => control.click());
  }
  expect(WRITE).not.toHaveBeenCalled();
}

function checkbox(id: string): HTMLButtonElement {
  const result = document.getElementById(id);
  if (!(result instanceof HTMLButtonElement)) throw new TypeError(`Checkbox ${id} missing`);
  return result;
}
