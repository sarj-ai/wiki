import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import type { DemoRewriteComparison } from "./demo-data";
import { DemoRewriteExample } from "./demo-rewrite-example";

const COMPARISON: DemoRewriteComparison = {
  afterText: "The bird sings from scrub.",
  afterWords: 125,
  beforeText: "The bird's song is heard while it remains hidden in dense scrub.",
  beforeWords: 250,
  scopeLabel: "Article",
};

it.each(["button", "Escape"])("returns focus when closed with %s", async (method) => {
  const view = render(<DemoRewriteExample comparison={COMPARISON} />);
  const trigger = screen.getByRole("button", { name: /50% fewer words/ });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Article: before and after" });
  expect(within(dialog).getByText(COMPARISON.beforeText)).toBeVisible();
  expect(within(dialog).getByText(COMPARISON.afterText)).toBeVisible();
  expect(within(dialog).getByText(/250 → 125 words/)).toBeVisible();
  expect(within(dialog).getByText("Article totals. Excerpts are verbatim.")).toBeVisible();
  const original = within(dialog).getByRole("region", { name: "Original passage" });
  const processed = within(dialog).getByRole("region", { name: "Processed passage" });
  original.focus();
  expect(original).toHaveFocus();
  processed.focus();
  expect(processed).toHaveFocus();
  if (method === "Escape") fireEvent.keyDown(processed, { key: "Escape" });
  else fireEvent.click(within(dialog).getByRole("button", { name: "Close comparison" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(trigger).toHaveFocus());
  view.unmount();
});

it("does not advertise a reduction when the recorded result has the same word count", () => {
  const view = render(<DemoRewriteExample comparison={{ ...COMPARISON, afterWords: 250 }} />);
  expect(screen.queryByRole("button")).toBeNull();
  view.unmount();
});
