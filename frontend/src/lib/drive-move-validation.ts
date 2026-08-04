import { MIME_FOLDER } from "@/types/wiki";

export function validateCrossDriveMove({
  destinationDriveId,
  mimeType,
  sourceDriveId,
}: CrossDriveMove): null | string {
  if (sourceDriveId === destinationDriveId || mimeType !== MIME_FOLDER) return null;
  return "Google Drive can’t move folders between drives. Move the folder’s files instead.";
}

interface CrossDriveMove {
  destinationDriveId: string;
  mimeType: string;
  sourceDriveId: string;
}
