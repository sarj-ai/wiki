import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { type QuickLinkItem, QuickLinks } from "./quick-links";

vi.mock("@/components/wiki-sidebar/use-document-intent", () => ({
  useDocumentIntent: () => ({}),
}));

const RECENT_ALIASES: QuickLinkItem[] = [
  {
    alias: "team-handbook",
    driveName: "People",
    fileId: "file-team",
    lastVisitedAt: "2026-08-04T15:00:00Z",
    mimeType: "application/vnd.google-apps.document",
    name: "Team Handbook",
  },
  {
    alias: "launch-plan",
    driveName: "Product",
    fileId: "file-launch",
    lastVisitedAt: "2026-08-03T12:00:00Z",
    mimeType: "application/vnd.google-apps.spreadsheet",
    name: "Launch Plan",
  },
];

describe("QuickLinks", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    host.remove();
    vi.restoreAllMocks();
  });

  it("searches across page name, short alias, and drive name", () => {
    render(root, <QuickLinks aliases={RECENT_ALIASES} />);
    const input = getSearchInput(host);

    typeInto(input, "Product");

    expect(host).toHaveTextContent("Launch Plan");
    expect(host).not.toHaveTextContent("Team Handbook");
    expect(host.querySelector("a")).toHaveAttribute("href", "/d/file-launch?go=launch-plan");
  });

  it("reuses case normalization across typing and refreshes it when accessible aliases change", () => {
    const aliases = RECENT_ALIASES.map((item) => ({ ...item }));
    const fields = new Set(
      aliases.flatMap((item) => [item.name, item.alias, item.driveName ?? ""]),
    );
    const normalize = vi.spyOn(String.prototype, "toLocaleLowerCase");
    render(root, <QuickLinks aliases={aliases} />);
    const input = getSearchInput(host);
    for (const query of ["pro", "produ", "product", "launch", "hand", "handbook"]) {
      typeInto(input, query);
    }
    const fieldCalls = normalize.mock.contexts.filter((value) => fields.has(String(value)));
    expect(fieldCalls).toHaveLength(6);
    expect(host).toHaveTextContent("Team Handbook");
    expect(host).not.toHaveTextContent("Launch Plan");
    render(
      root,
      <QuickLinks
        aliases={aliases.map((item) => ({
          ...item,
          alias: "current-page",
          name: "Current page",
        }))}
      />,
    );
    expect(host).toHaveTextContent("No matching quick links");
    typeInto(getSearchInput(host), "CURRENT");
    expect(host.querySelectorAll("a")).toHaveLength(2);
    expect(host).toHaveTextContent("Current page");
  });

  it("supports arrow-key selection and Enter to open", () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => null);
    render(root, <QuickLinks aliases={RECENT_ALIASES} />);
    const input = getSearchInput(host);

    keyDown(input, "ArrowDown");
    expect(host.querySelector('a[href="/d/file-launch?go=launch-plan"]')).toHaveAttribute(
      "aria-selected",
      "true",
    );
    keyDown(input, "Enter");

    expect(click).toHaveBeenCalledOnce();
    expect(click.mock.instances[0]).toHaveAttribute("href", "/d/file-launch?go=launch-plan");
  });

  it("focuses search with the global slash shortcut", () => {
    render(root, <QuickLinks aliases={RECENT_ALIASES} />);
    const input = getSearchInput(host);
    input.blur();

    window.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "/" }));

    expect(input).toHaveFocus();
  });

  it("keeps mobile results as full-row links with page, drive, and alias visible", () => {
    const firstAlias = RECENT_ALIASES[0];
    if (firstAlias === undefined) throw new Error("Recent alias fixture missing");
    render(root, <QuickLinks aliases={[firstAlias]} />);

    const link = host.querySelector("a");
    expect(link).toHaveClass("min-h-16");
    expect(link).toHaveTextContent("Team Handbook");
    expect(link).toHaveTextContent("People");
    expect(link).toHaveTextContent("go/team-handbook");
    expect(link?.querySelector("time")).toHaveClass("hidden", "sm:block");
  });

  it("shows a non-disclosing empty state", () => {
    render(root, <QuickLinks aliases={[]} />);

    expect(host).toHaveTextContent("No recent quick links");
    expect(host).toHaveTextContent("when you still have access");
    expect(getSearchInput(host)).toBeDisabled();
  });

  it("shows a retryable error state without rendering results", () => {
    render(root, <QuickLinks aliases={RECENT_ALIASES} error />);

    expect(host).toHaveTextContent("Unable to load quick links");
    expect(host).toHaveTextContent("Try refreshing the page");
    expect(host.querySelector("a")).not.toBeInTheDocument();
    expect(getSearchInput(host)).toBeDisabled();
  });
});

function render(root: Root, element: React.ReactNode) {
  flushSync(() => {
    root.render(element);
  });
}

function getSearchInput(host: HTMLElement): HTMLInputElement {
  const input = host.querySelector<HTMLInputElement>('input[aria-label="Search quick links"]');
  if (input === null) throw new Error("Search input not found");
  return input;
}

function typeInto(input: HTMLInputElement, value: string) {
  // eslint-disable-next-line @typescript-eslint/unbound-method -- The native setter is rebound immediately.
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  if (setter === undefined) throw new Error("Input value setter not found");
  flushSync(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function keyDown(input: HTMLInputElement, key: string) {
  flushSync(() => {
    input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key }));
  });
}
