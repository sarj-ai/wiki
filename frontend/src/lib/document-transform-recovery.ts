import * as z from "zod";

const TransformModeSchema = z.enum(["both", "format", "unslop"]);
const TransformOutputStrategySchema = z.enum(["copy", "in_place"]);
const TransformRecoverySchema = z.object({
  createdAt: z.number().nonnegative(),
  key: z.uuid(),
  mode: TransformModeSchema,
  outputStrategy: TransformOutputStrategySchema,
  outputName: z.string().nullish(),
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

const SharedRecoverySchema = z.discriminatedUnion("kind", [
  z
    .object({ kind: z.literal("pending"), operation: TransformRecoverySchema })
    .strict(),
  z
    .object({
      kind: z.literal("completed"),
      operation: TransformRecoverySchema,
      completedAt: z.number().nonnegative(),
    })
    .strict(),
]);
const LegacySharedRecoverySchema = z
  .object({
    operation: TransformRecoverySchema,
    completedAt: z.number().nonnegative().nullable(),
  })
  .strict();

function sharedKey(owner: string, source: string): string {
  return `${recoveryKey(owner, source)}:shared`;
}

function readShared(owner: string, source: string) {
  const raw = window.localStorage.getItem(sharedKey(owner, source));
  if (!raw) return null;
  const value: unknown = JSON.parse(raw);
  const legacy = LegacySharedRecoverySchema.safeParse(value);
  if (!legacy.success) return SharedRecoverySchema.parse(value);
  return SharedRecoverySchema.parse(
    legacy.data.completedAt === null
      ? { kind: "pending", operation: legacy.data.operation }
      : { kind: "completed", ...legacy.data },
  );
}

export async function claimTransformRecovery(
  owner: null | string,
  source: string,
  candidate: TransformRecovery,
): Promise<TransformRecovery> {
  if (!source.startsWith("drive:")) return candidate;
  if (!owner) throw new Error("Sign in before polishing a document.");

  const browser: Partial<Navigator> = navigator;
  const locks = browser.locks;
  if (!locks) {
    const existing = readShared(owner, source);
    if (existing?.kind === "pending") return existing.operation;
    throw new Error(
      "Safe cross-tab polish requires a browser with Web Locks enabled.",
    );
  }
  return locks.request(sharedKey(owner, source), () => {
    const existing = readShared(owner, source);
    if (
      existing &&
      (existing.kind === "pending" ||
        candidate.createdAt <= existing.completedAt)
    ) {
      return existing.operation;
    }
    window.localStorage.setItem(
      sharedKey(owner, source),
      JSON.stringify({ kind: "pending", operation: candidate }),
    );
    return candidate;
  });
}

export async function completeTransformRecovery(
  owner: null | string,
  source: string,
  key: string,
): Promise<void> {
  if (!owner || !source.startsWith("drive:")) return;
  const browser: Partial<Navigator> = navigator;
  const locks = browser.locks;
  if (!locks) return;
  await locks.request(sharedKey(owner, source), () => {
    const existing = readShared(owner, source);
    if (existing?.operation.key !== key || existing.kind !== "pending") return;
    window.localStorage.setItem(
      sharedKey(owner, source),
      JSON.stringify({
        kind: "completed",
        operation: existing.operation,
        completedAt: Date.now(),
      }),
    );
  });
}

export async function recordTransformTask(
  owner: null | string,
  source: string,
  operation: TransformRecovery & { taskId: string },
): Promise<void> {
  if (!owner || !source.startsWith("drive:")) return;
  const browser: Partial<Navigator> = navigator;
  if (!browser.locks) return;
  await browser.locks.request(sharedKey(owner, source), () => {
    const existing = readShared(owner, source);
    if (
      existing?.operation.key !== operation.key ||
      existing.kind !== "pending"
    )
      return;
    if (
      existing.operation.taskId &&
      existing.operation.taskId !== operation.taskId
    ) {
      throw new Error(
        "The saved polish task does not match the admitted operation.",
      );
    }
    window.localStorage.setItem(
      sharedKey(owner, source),
      JSON.stringify({
        ...existing,
        operation: { ...existing.operation, taskId: operation.taskId },
      }),
    );
  });
}

export function readTransformRecovery(
  owner: null | string,
  source: string,
): null | TransformRecovery {
  if (!owner) return null;
  if (source.startsWith("drive:")) {
    const shared = readShared(owner, source);
    if (shared?.kind === "pending") return shared.operation;
  }
  const raw = window.sessionStorage.getItem(recoveryKey(owner, source));
  if (!raw) return null;

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
