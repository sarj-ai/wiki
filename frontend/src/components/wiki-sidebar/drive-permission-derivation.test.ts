import type { DrivePermission, DrivePermissionsPage, DrivePrincipalType } from "@/types/wiki";

import { derivePermissions } from "./drive-access-sheet";

describe("paginated access derivation", () => {
  it("uses the latest duplicate without changing broad-access order and retains role/name ordering", () => {
    const domain = permission("domain", "domain", "reader", "Domain");
    const anyone = permission("anyone", "anyone", "reader", "Anyone");
    const old = permission("duplicate", "user", "reader", "Old");
    const updated = permission("duplicate", "group", "writer", "Zebra");
    const manager = permission("manager", "user", "organizer", "Manager");
    const alpha = permission("alpha", "user", "writer", "Alpha");
    const unknown = permission("unknown", "user", "futureRole", "Future");
    const pages = [page([domain, old, anyone, unknown]), page([updated, manager, alpha])];
    const result = derivePermissions(pages);
    expect(result.count).toBe(6);
    expect(result.generalAccess).toEqual([domain, anyone]);
    expect(result.people).toEqual([manager, alpha, updated, unknown]);
    expect(pages[0]?.permissions).toEqual([domain, old, anyone, unknown]);
    expect(
      derivePermissions([page([{ ...updated, displayName: "Renamed" }])]).people[0]?.displayName,
    ).toBe("Renamed");
  });

  it("reads each deduplicated principal type once for ten pages of 100 entries", () => {
    let reads = 0;
    const pages = Array.from({ length: 10 }, (_pageValue, pageIndex) =>
      page(
        Array.from({ length: 100 }, (_entryValue, index) => ({
          ...permission(
            `permission-${pageIndex * 100 + index}`,
            "user",
            "reader",
            `Member ${index}`,
          ),
          get principalType() {
            reads += 1;
            return "user" as const;
          },
        })),
      ),
    );
    const result = derivePermissions(pages);
    expect(result.count).toBe(1_000);
    expect(result.people).toHaveLength(1_000);
    expect(reads).toBe(1_000);
    expect(derivePermissions([])).toEqual({ count: 0, generalAccess: [], people: [] });
  });
});

function page(permissions: DrivePermission[]): DrivePermissionsPage {
  return {
    accessModel: "shared_drive_membership",
    driveId: "drive",
    nextPageToken: undefined,
    permissions,
  };
}

function permission(
  id: string,
  principalType: DrivePrincipalType,
  role: string,
  displayName: string,
): DrivePermission {
  return {
    allowFileDiscovery: undefined,
    deleted: false,
    displayName,
    domain: undefined,
    emailAddress: undefined,
    expirationTime: undefined,
    id,
    pendingOwner: false,
    principalType,
    role,
  };
}
