import * as z from "zod";

import type { TransformRecovery } from "@/lib/document-transform-recovery";
import { MARKDOWN_LIMIT_BYTES, markdownByteLength } from "@/lib/markdown-import";
import { TransformModeSchema } from "@/lib/transform-mode-schema";

export const REPLAY_MAX_AGE_MS = 6 * 24 * 60 * 60 * 1000;
const MarkdownSourceSchema = z.strictObject({
  kind: z.literal("markdown"),
  content: z
    .string()
    .min(1)
    .max(MARKDOWN_LIMIT_BYTES)
    .refine((value) => markdownByteLength(value) <= MARKDOWN_LIMIT_BYTES),
  name: z.string().min(1).max(255),
  destinationFolderId: z.string().min(1).max(255),
});
const UploadedMarkdownSourceSchema = z.strictObject({
  kind: z.literal("uploaded_markdown"),
  uploadId: z.uuid(),
  name: z.string().min(1).max(255),
  destinationFolderId: z.string().min(1).max(255),
});
const BundleSchema = z.strictObject({
  version: z.literal(1),
  owner: z.string().min(1),
  key: z.uuid(),
  createdAt: z.number().nonnegative(),
  mode: TransformModeSchema,
  outputName: z.string().nullable(),
  source: z.discriminatedUnion("kind", [MarkdownSourceSchema, UploadedMarkdownSourceSchema]),
});
export type MarkdownRecoveryBundle = z.infer<typeof BundleSchema>;

export function markdownRecoveryStorageKey(owner: string): string {
  return `sarj-wiki:transform-draft:${encodeURIComponent(owner)}`;
}

export function validateMarkdownRecovery(
  raw: string,
  owner: string,
  operation: TransformRecovery,
): MarkdownRecoveryBundle {
  if (raw.length > 4 * 1024 * 1024) throw new Error("Recovery draft exceeds the safe limit");
  const parsed = BundleSchema.safeParse(JSON.parse(raw));
  if (!parsed.success) throw new Error("Recovery draft is invalid");
  const bundle = parsed.data;
  if (
    bundle.owner !== owner ||
    bundle.key !== operation.key ||
    bundle.createdAt !== operation.createdAt ||
    bundle.mode !== operation.mode ||
    bundle.outputName !== (operation.outputName ?? null) ||
    operation.outputStrategy !== "copy" ||
    operation.source !== null ||
    Date.now() - bundle.createdAt >= REPLAY_MAX_AGE_MS ||
    bundle.createdAt > Date.now()
  ) {
    throw new Error("Recovery draft does not match this reserved operation or has expired");
  }
  return bundle;
}

export function makeMarkdownRecovery(
  owner: string,
  operation: TransformRecovery,
  source: unknown,
): MarkdownRecoveryBundle {
  return validateMarkdownRecovery(
    JSON.stringify({
      version: 1,
      owner,
      key: operation.key,
      createdAt: operation.createdAt,
      mode: operation.mode,
      outputName: operation.outputName ?? null,
      source,
    }),
    owner,
    operation,
  );
}

export function saveMarkdownRecovery(bundle: MarkdownRecoveryBundle): void {
  const raw = JSON.stringify(bundle);
  const key = markdownRecoveryStorageKey(bundle.owner);
  window.sessionStorage.setItem(key, raw);
  if (window.sessionStorage.getItem(key) !== raw)
    throw new Error("Recovery draft could not be saved");
}

export function clearMarkdownRecovery(owner: string): void {
  try {
    window.sessionStorage.removeItem(markdownRecoveryStorageKey(owner));
  } catch {
    // Cleanup cannot undo an admitted task or prevent its UUID being recovered.
  }
}
