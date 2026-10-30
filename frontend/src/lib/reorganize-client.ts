import * as z from "zod";

import { proposeFileReorganizationAction } from "@/app/actions/smart-create-actions";
import { runAction } from "@/lib/action-client";

const DestinationSchema = z
  .object({
    drive_id: z.string(),
    drive_name: z.string(),
    folder_id: z.string(),
    folder_path: z.array(z.string()),
  })
  .strict();

const ProposalSchema = z
  .object({
    current_destination: DestinationSchema,
    reason: z.string().nullable(),
    suggested_destination: DestinationSchema.nullable(),
  })
  .strict();

export interface ReorganizationDestination {
  driveId: string;
  driveName: string;
  folderId: string;
  folderPath: string[];
}

export interface ReorganizationProposal {
  currentDestination: ReorganizationDestination;
  reason?: string;
  suggestedDestination?: ReorganizationDestination;
}

export async function proposeFileReorganization(fileId: string): Promise<ReorganizationProposal> {
  const body = await runAction(async () => await proposeFileReorganizationAction(fileId));
  const proposal = ProposalSchema.parse(JSON.parse(body));
  return {
    currentDestination: toDestination(proposal.current_destination),
    ...(!(proposal.reason === null) && { reason: proposal.reason }),
    ...(!(proposal.suggested_destination === null) && {
      suggestedDestination: toDestination(proposal.suggested_destination),
    }),
  };
}

function toDestination(value: z.output<typeof DestinationSchema>): ReorganizationDestination {
  return {
    driveId: value.drive_id,
    driveName: value.drive_name,
    folderId: value.folder_id,
    folderPath: value.folder_path,
  };
}
