import { z } from "zod";

const ZEXPANSION_MAP = z.record(z.string(), z.literal(true));

export const ZLAST_REFRESH_MS = z.coerce.number().int().nonnegative().catch(0);

const ZDOC_REF = z.object({
  fileId: z.string().min(1),
  mimeType: z.string(),
  title: z.string(),
});

export const ZRECENT_DOCS = z.array(ZDOC_REF).catch([]);

const ZRECENT_ALIAS_VISIT = z.object({
  alias: z
    .string()
    .min(5)
    .max(32)
    .regex(/^[a-z]{2,12}(?:-[a-z]{2,12}){1,2}$/),
  visitedAt: z.iso.datetime(),
});

export const ZRECENT_ALIAS_VISITS = z.array(ZRECENT_ALIAS_VISIT).max(50).catch([]);

export const ZSIDEBAR_WIDTH_CSS = z
  .string()
  .regex(/^\d+px$/)
  .nullable()
  .catch(null);

export const SIDEBAR_WIDTH_COOKIE = "sidebar_width";

export const ZSIDEBAR_STATE_COOKIE = z
  .enum(["true", "false"])
  .catch("true")
  .transform((v) => v === "true");

export const ZAUTH_ERROR_PARAM = z.enum(["domain_restricted", "auth_failed", "server_error"]);

export const ZPERSISTED_EXPANSION = z.object({
  state: z.record(z.string(), ZEXPANSION_MAP),
  version: z.number().int(),
});

export const ZPERSISTED_DRIVE_EXPANSION = z.object({
  state: z.object({
    expanded: ZEXPANSION_MAP,
    initialized: z.boolean(),
  }),
  version: z.literal(1),
});
