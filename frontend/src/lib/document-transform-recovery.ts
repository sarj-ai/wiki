import * as z from "zod";

const TransformModeSchema = z.enum(["both", "format", "unslop"]);
const TransformOutputStrategySchema = z.enum(["copy", "in_place"]);
const TransformRecoverySchema = z.object({
  createdAt: z.number().nonnegative(),
  key: z.uuid(),
  mode: TransformModeSchema,
  outputStrategy: TransformOutputStrategySchema,
  source: z
    .object({
      expectedModifiedTime: z.string(),
      fileId: z.string(),
      kind: z.literal("drive_file"),
      name: z.string(),
    })
    .nullable(),
  taskId: z.uuid().nullable(),
});

export type TransformRecovery = z.infer<typeof TransformRecoverySchema>;

export function readTransformRecovery(
  owner: null | string,
  source: string,
): null | TransformRecovery {
  if (!owner) return null;
  const raw = window.sessionStorage.getItem(recoveryKey(owner, source));
  if (!raw) return null;
  // Invalid persisted state must fail closed instead of silently allowing a duplicate.
  return TransformRecoverySchema.parse(JSON.parse(raw));
}

export function writeTransformRecovery(
  owner: null | string,
  source: string,
  operation: null | TransformRecovery,
): void {
  if (!owner) return;
  const key = recoveryKey(owner, source);
  if (operation) window.sessionStorage.setItem(key, JSON.stringify(operation));
  else window.sessionStorage.removeItem(key);
}

function recoveryKey(owner: string, source: string): string {
  return `sarj-wiki:transform:${encodeURIComponent(owner)}:${encodeURIComponent(source)}`;
}
