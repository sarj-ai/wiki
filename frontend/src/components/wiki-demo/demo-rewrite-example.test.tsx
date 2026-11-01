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

it("opens the recorded passages and returns focus when the comparison closes", async () => {
  const view = render(<DemoRewriteExample comparison={COMPARISON} />);
  const trigger = screen.getByRole("button", { name: /50% fewer words/ });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Article: before and after" });
  expect(within(dialog).getByText(COMPARISON.beforeText)).toBeVisible();
  expect(within(dialog).getByText(COMPARISON.afterText)).toBeVisible();
  expect(within(dialog).getByText(/250 → 125 words/)).toBeVisible();
  fireEvent.click(within(dialog).getByRole("button", { name: "Close comparison" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(trigger).toHaveFocus());
  view.unmount();
});

it("does not advertise a reduction when the recorded result has the same word count", () => {
  const view = render(<DemoRewriteExample comparison={{ ...COMPARISON, afterWords: 250 }} />);
  expect(screen.queryByRole("button")).toBeNull();
  view.unmount();
});
