import { proposeFileReorganizationAction } from "@/app/actions/smart-create-actions";

import { proposeFileReorganization } from "./reorganize-client";

vi.mock("@/app/actions/smart-create-actions", () => ({
  proposeFileReorganizationAction: vi.fn(),
}));

const proposeAction = vi.mocked(proposeFileReorganizationAction);

it("maps a same-drive folder recommendation", async () => {
  proposeAction.mockResolvedValue({
    data: JSON.stringify({
      current_destination: {
        drive_id: "product",
        drive_name: "Product",
        folder_id: "product",
        folder_path: ["Product"],
      },
      reason: "Roadmaps belong with product planning.",
      suggested_destination: {
        drive_id: "product",
        drive_name: "Product",
        folder_id: "roadmaps",
        folder_path: ["Product", "Roadmaps"],
      },
    }),
    ok: true,
  });

  await expect(proposeFileReorganization("document")).resolves.toEqual({
    currentDestination: {
      driveId: "product",
      driveName: "Product",
      folderId: "product",
      folderPath: ["Product"],
    },
    reason: "Roadmaps belong with product planning.",
    suggestedDestination: {
      driveId: "product",
      driveName: "Product",
      folderId: "roadmaps",
      folderPath: ["Product", "Roadmaps"],
    },
  });
});
