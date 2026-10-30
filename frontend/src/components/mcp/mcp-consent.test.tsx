import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { McpConsent } from "./mcp-consent";

function maybeRole(
  container: HTMLElement,
  role: string,
  options?: { name: string },
): HTMLElement | null {
  return (
    [...container.querySelectorAll<HTMLElement>("button, [role]")].find((element) => {
      const actual =
        element.getAttribute("role") ?? (element.tagName === "BUTTON" ? "button" : null);
      return (
        actual === role && (options === undefined || element.textContent.trim() === options.name)
      );
    }) ?? null
  );
}

function findRole(container: HTMLElement, role: string, options?: { name: string }): HTMLElement {
  const element = maybeRole(container, role, options);
  if (element === null) throw new Error(`Missing ${role}: ${options?.name ?? ""}`);
  return element;
}

describe("McpConsent", () => {
  let container: HTMLDivElement;
  let root: Root;
  const onDecision =
    vi.fn<
      (decision: { approve: boolean; transformApproved: boolean }) => Promise<"approved" | "denied">
    >();
  const onRecover = vi.fn<() => Promise<"approved" | "denied" | "expired" | "pending">>();

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    onDecision.mockReset().mockResolvedValue("approved");
    onRecover.mockReset().mockResolvedValue("pending");
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    container.remove();
  });

  function render(requestsTransform = true) {
    flushSync(() =>
      root.render(
        <McpConsent
          accountEmail="person@example.com"
          clientName='<img src=x onerror="alert(1)">'
          onDecision={onDecision}
          onRecover={onRecover}
          requestsTransform={requestsTransform}
        />,
      ),
    );
  }

  function click(name: string) {
    findRole(container, "button", { name }).click();
  }

  it("defaults to read access and escapes the requesting app name", async () => {
    render();
    expect(findRole(container, "checkbox")).not.toBeChecked();
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent('<img src=x onerror="alert(1)">');
    click("Allow read access");
    await vi.waitFor(() =>
      expect(onDecision).toHaveBeenCalledExactlyOnceWith({
        approve: true,
        transformApproved: false,
      }),
    );
    await vi.waitFor(() =>
      expect(findRole(container, "status")).toHaveTextContent("No document has been changed"),
    );
  });

  it("requires explicit editing opt-in and names that permission on approval", async () => {
    render();
    flushSync(() => findRole(container, "checkbox").click());
    click("Allow read and edit access");
    await vi.waitFor(() =>
      expect(onDecision).toHaveBeenCalledExactlyOnceWith({
        approve: true,
        transformApproved: true,
      }),
    );
  });

  it("does not offer editing when the app requested only reading", () => {
    render(false);
    expect(maybeRole(container, "checkbox")).toBeNull();
    expect(findRole(container, "button", { name: "Allow read access" })).toBeEnabled();
  });

  it("blocks double-clicks and competing decisions before React rerenders", async () => {
    render();
    click("Allow read access");
    click("Allow read access");
    click("Deny");
    expect(onDecision).toHaveBeenCalledOnce();
    await vi.waitFor(() => expect(findRole(container, "button", { name: "Deny" })).toBeDisabled());
  });

  it("never includes editing approval in a denial", async () => {
    onDecision.mockResolvedValue("denied");
    render();
    flushSync(() => findRole(container, "checkbox").click());
    click("Deny");
    await vi.waitFor(() =>
      expect(onDecision).toHaveBeenCalledExactlyOnceWith({
        approve: false,
        transformApproved: false,
      }),
    );
    await vi.waitFor(() =>
      expect(findRole(container, "status")).toHaveTextContent("Access denied"),
    );
  });

  it("keeps approval locked after an uncertain result until pending is confirmed", async () => {
    onDecision.mockRejectedValueOnce(new Error("network"));
    render();
    click("Allow read access");
    await vi.waitFor(() =>
      expect(findRole(container, "alert")).toHaveTextContent("could not confirm"),
    );
    expect(findRole(container, "button", { name: "Allow read access" })).toBeDisabled();
    click("Check status");
    click("Check status");
    expect(onRecover).toHaveBeenCalledOnce();
    await vi.waitFor(() =>
      expect(findRole(container, "button", { name: "Allow read access" })).toBeEnabled(),
    );
    click("Allow read access");
    await vi.waitFor(() => expect(onDecision).toHaveBeenCalledTimes(2));
  });

  it.each(["approved", "denied", "expired"] as const)(
    "does not retry a recovered %s decision",
    async (status) => {
      onDecision.mockRejectedValueOnce(new Error("network"));
      onRecover.mockResolvedValue(status);
      render();
      click("Allow read access");
      await vi.waitFor(() => expect(findRole(container, "alert")).toBeInTheDocument());
      click("Check status");
      await vi.waitFor(() =>
        expect(maybeRole(container, "button", { name: "Check status" })).toBeNull(),
      );
      expect(findRole(container, "button", { name: "Allow read access" })).toBeDisabled();
      expect(onDecision).toHaveBeenCalledOnce();
    },
  );
});
