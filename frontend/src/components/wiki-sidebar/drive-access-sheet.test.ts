import { drivePermissionsPage } from "@/lib/wiki-schemas";

import { roleLabel } from "./drive-access-sheet";

describe("drive access presentation", () => {
  it.each([
    ["organizer", "Manager"],
    ["fileOrganizer", "Content manager"],
    ["writer", "Contributor"],
    ["commenter", "Commenter"],
    ["reader", "Viewer"],
    ["futureRole", "future role"],
  ])("maps %s to %s", (role, expected) => {
    expect(roleLabel(role)).toBe(expected);
  });

  it("maps optional principal identity fields without inventing values", () => {
    const page = drivePermissionsPage.parse({
      access_model: "shared_drive_membership",
      drive_id: "drive-1",
      next_page_token: "next",
      permissions: [
        {
          allow_file_discovery: null,
          deleted: false,
          display_name: null,
          domain: null,
          email_address: "team@example.com",
          expiration_time: null,
          id: "permission-1",
          pending_owner: false,
          principal_type: "group",
          role: "reader",
        },
      ],
    });

    expect(page).toEqual({
      accessModel: "shared_drive_membership",
      driveId: "drive-1",
      nextPageToken: "next",
      permissions: [
        {
          allowFileDiscovery: undefined,
          deleted: false,
          displayName: undefined,
          domain: undefined,
          emailAddress: "team@example.com",
          expirationTime: undefined,
          id: "permission-1",
          pendingOwner: false,
          principalType: "group",
          role: "reader",
        },
      ],
    });
  });
});
