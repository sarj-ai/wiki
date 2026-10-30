import type { Route } from "next";
import { RouterContext } from "next/dist/shared/lib/router-context.shared-runtime";
import Link from "next/link";
import type { NextRouter } from "next/router";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { SidebarProvider, useSidebar } from "@/components/ui/sidebar";
import {
  SidebarNavigationProvider,
  useSidebarNavigation,
} from "@/components/wiki-sidebar/use-sidebar-navigation";

const ROUTER: NextRouter = {
  asPath: "/d/current",
  basePath: "",
  pathname: "/d/current",
  query: {},
  route: "/d/current",
  isFallback: false,
  isLocaleDomain: false,
  isPreview: false,
  isReady: true,
  back: vi.fn(),
  beforePopState: vi.fn(),
  forward: vi.fn(),
  reload: vi.fn(),
  prefetch: vi.fn<NextRouter["prefetch"]>().mockResolvedValue(undefined),
  push: vi.fn<NextRouter["push"]>().mockResolvedValue(true),
  replace: vi.fn<NextRouter["replace"]>().mockResolvedValue(true),
  events: { emit: vi.fn(), off: vi.fn(), on: vi.fn() },
};

function Links({
  cancelClick = false,
  cancelNavigate = false,
  target = "/d/next",
}: {
  cancelClick?: boolean;
  cancelNavigate?: boolean;
  target?: Route<"/d/current" | "/d/next">;
}) {
  const { openMobile, setOpenMobile } = useSidebar();
  const navigate = useSidebarNavigation();
  return (
    <>
      <button onClick={() => setOpenMobile(true)} type="button">
        Open mobile sidebar
      </button>
      <output>{openMobile ? "Sidebar open" : "Sidebar closed"}</output>
      <Link
        href={target}
        onClick={(event) => {
          if (cancelClick) event.preventDefault();
        }}
        onNavigate={(event) => {
          if (cancelNavigate) {
            event.preventDefault();
            return;
          }
          navigate(target);
        }}
        prefetch={false}
      >
        Select destination
      </Link>
    </>
  );
}

describe("mobile sidebar native accepted navigation", () => {
  let host: HTMLDivElement;
  let root: Root;
  let initialWidth: number;

  beforeEach(() => {
    initialWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    vi.mocked(ROUTER.push).mockClear().mockResolvedValue(true);
    vi.mocked(ROUTER.replace).mockClear().mockResolvedValue(true);
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    // Suppress the test DOM's native anchor action after React/Next handles
    // eligibility, so external and modified controls do not navigate the fixture.
    host.addEventListener("click", (event) => event.preventDefault());
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    host.remove();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: initialWidth });
  });

  function render({
    ownerKey = "user-a",
    pathname = "/d/current",
    ...props
  }: React.ComponentProps<typeof Links> & { ownerKey?: string; pathname?: string } = {}) {
    ROUTER.pathname = pathname;
    ROUTER.asPath = pathname;
    flushSync(() =>
      root.render(
        <RouterContext value={ROUTER}>
          <SidebarProvider>
            <SidebarNavigationProvider ownerKey={ownerKey} pathname={pathname}>
              <Links {...props} />
            </SidebarNavigationProvider>
          </SidebarProvider>
        </RouterContext>,
      ),
    );
  }

  function open() {
    const element = host.querySelector("button");
    if (!(element instanceof HTMLButtonElement)) throw new TypeError("Open control missing");
    flushSync(() => element.click());
    expect(host).toHaveTextContent("Sidebar open");
  }

  function select(init: MouseEventInit = {}) {
    const element = host.querySelector("a");
    if (!(element instanceof HTMLAnchorElement)) throw new TypeError("Destination missing");
    flushSync(() =>
      element.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init }),
      ),
    );
  }

  it("keeps a changed-route drawer open until the accepted destination commits", async () => {
    render();
    open();
    select();
    expect(ROUTER.push).toHaveBeenCalledOnce();
    expect(host).toHaveTextContent("Sidebar open");
    render();
    expect(host).toHaveTextContent("Sidebar open");
    render({ pathname: "/d/next" });
    await vi.waitFor(() => expect(host).toHaveTextContent("Sidebar closed"));
  });

  it.each(["/d/current", "/"] as const)(
    "closes immediately for an accepted current-route %s selection",
    (target) => {
      render({ pathname: target, target });
      open();
      select();
      expect(ROUTER.push).toHaveBeenCalledOnce();
      expect(host).toHaveTextContent("Sidebar closed");
    },
  );

  it("closes the logo destination after home commits", async () => {
    render({ target: "/" });
    open();
    select();
    expect(host).toHaveTextContent("Sidebar open");
    render({ target: "/", pathname: "/" });
    await vi.waitFor(() => expect(host).toHaveTextContent("Sidebar closed"));
  });

  it("leaves the drawer open when an accepted ROUTER call fails without a URL commit", () => {
    vi.mocked(ROUTER.push).mockResolvedValue(false);
    render();
    open();
    select();
    render();
    expect(ROUTER.push).toHaveBeenCalledOnce();
    expect(host).toHaveTextContent("Sidebar open");
  });

  it.each(["click", "navigate"])("preserves a consumer-cancelled %s without closing", (cancel) => {
    render({ cancelClick: cancel === "click", cancelNavigate: cancel === "navigate" });
    open();
    select();
    expect(ROUTER.push).not.toHaveBeenCalled();
    expect(host).toHaveTextContent("Sidebar open");
  });

  it.each([{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }])(
    "keeps modified native link activation open ($ctrlKey $metaKey $shiftKey $altKey)",
    (modifiers) => {
      render();
      open();
      select(modifiers);
      expect(ROUTER.push).not.toHaveBeenCalled();
      expect(host).toHaveTextContent("Sidebar open");
    },
  );

  it("keeps external native link activation open", () => {
    render({ target: "https://docs.google.com/forms/example" });
    open();
    select();
    expect(ROUTER.push).not.toHaveBeenCalled();
    expect(host).toHaveTextContent("Sidebar open");
  });

  it("retires an old owner's pending destination before a later matching path appears", () => {
    render();
    open();
    select();
    render({ ownerKey: "user-b" });
    render({ ownerKey: "user-b", pathname: "/d/next" });
    expect(host).toHaveTextContent("Sidebar open");
  });

  it("does not close a different committed target or later consume its discarded pending intent", () => {
    render();
    open();
    select();
    render({ pathname: "/d/other" });
    expect(host).toHaveTextContent("Sidebar open");
    render({ pathname: "/d/next" });
    expect(host).toHaveTextContent("Sidebar open");
  });

  it("does not change desktop sidebar state on the same native link contract", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1200 });
    render();
    open();
    select();
    render({ pathname: "/d/next" });
    expect(host).toHaveTextContent("Sidebar open");
  });
});
