import { useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import type { useDrivePermissions } from "@/hooks/use-wiki-tree";
import { drivePermissionsPage } from "@/lib/wiki-schemas";
import type { DriveSummary } from "@/types/wiki";

import { DriveAccessSheet } from "./drive-access-sheet";

type AccessState = Pick<ReturnType<typeof useDrivePermissions>, "data" | "isError" | "isPending">;

const QUERY = vi.hoisted(() => ({
  read: vi.fn(),
  retry: vi.fn<() => Promise<void>>(),
}));

let access: AccessState;
const ACCESS_LISTENERS = new Set<() => void>();
vi.mock("@/hooks/use-wiki-tree", () => ({
  useDrivePermissions: (driveId: null | string, enabled: boolean) => {
    QUERY.read(driveId, enabled);
    const state = useSyncExternalStore(subscribeToAccess, getAccessSnapshot, getAccessSnapshot);
    return {
      ...state,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      refetch: QUERY.retry,
    };
  },
}));

const DRIVE: DriveSummary = { id: "drive-1", isPersonal: false, name: "Fixture shared drive" };

describe("drive access request feedback", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    access = { data: undefined, isError: false, isPending: true };
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    flushSync(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("announces loading instead of an empty result until the member list arrives", () => {
    openSheet(DRIVE);
    expect(description()).toHaveTextContent("Loading access…");
    expect(document.querySelector('[role="dialog"]')).not.toHaveTextContent(
      "No access entries returned",
    );
    flushSync(() => {
      access = {
        data: { pageParams: [undefined], pages: [permissionsPage(2)] },
        isError: false,
        isPending: false,
      };
      for (const listener of ACCESS_LISTENERS) listener();
    });
    expect(description()).toHaveTextContent("2 access entries");
    expect(document.querySelector('[role="dialog"]')).toHaveTextContent("Fixture person 1");
    expect(description()).not.toHaveTextContent("Loading access…");
  });

  it("reports a failed request and preserves Retry instead of claiming an empty member list", () => {
    access = { data: undefined, isError: true, isPending: false };
    openSheet(DRIVE);
    expect(description()).toHaveTextContent("Couldn't load drive access");
    expect(document.querySelector('[role="dialog"]')).not.toHaveTextContent(
      "No access entries returned",
    );
    const retry = [...document.querySelectorAll("button")].find(
      (button) => button.textContent.trim() === "Retry",
    );
    if (!retry) throw new Error("Access retry control missing");
    flushSync(() => retry.click());
    expect(QUERY.retry).toHaveBeenCalledOnce();
  });

  it("reserves the empty summary for a successfully returned empty list", () => {
    access = {
      data: { pageParams: [undefined], pages: [permissionsPage(0)] },
      isError: false,
      isPending: false,
    };
    openSheet(DRIVE);
    expect(description()).toHaveTextContent("No access entries returned");
  });

  function openSheet(drive: DriveSummary) {
    flushSync(() => root.render(<DriveAccessSheet drive={drive} />));
    const trigger = container.querySelector("button");
    if (!trigger) throw new Error("Drive access trigger missing");
    flushSync(() => trigger.click());
  }
});

function subscribeToAccess(onStoreChange: () => void) {
  ACCESS_LISTENERS.add(onStoreChange);
  return () => {
    ACCESS_LISTENERS.delete(onStoreChange);
  };
}

function getAccessSnapshot() {
  return access;
}

function description() {
  const element = document.querySelector('[data-slot="sheet-description"]');
  if (!element) throw new Error("Drive access description missing");
  return element;
}

function permissionsPage(count: number) {
  return drivePermissionsPage.parse({
    access_model: "shared_drive_membership",
    drive_id: "drive-1",
    next_page_token: null,
    permissions: Array.from({ length: count }, (_, index) => ({
      allow_file_discovery: null,
      deleted: false,
      display_name: `Fixture person ${index}`,
      domain: null,
      email_address: `fixture-${index}@example.test`,
      expiration_time: null,
      id: `permission-${index}`,
      pending_owner: false,
      principal_type: "user",
      role: "reader",
    })),
  });
}
