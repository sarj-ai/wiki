import { z } from "zod";

const ExpansionMapSchema = z.record(z.string(), z.literal(true));

export const LastRefreshMsSchema = z.coerce.number().int().nonnegative().catch(0);

const DocRefSchema = z.object({
  fileId: z.string().min(1),
  mimeType: z.string(),
  title: z.string(),
});

export const RecentDocsSchema = z
  .array(DocRefSchema)
  .max(6)
  .catch(() => []);

export const RecentAliasVisitSchema = z.object({
  alias: z
    .string()
    .min(5)
    .max(32)
    .regex(/^[a-z]{2,12}(?:-[a-z]{2,12}){1,2}$/),
  visitedAt: z.iso.datetime(),
});

export const RecentAliasVisitsSchema = z
  .array(RecentAliasVisitSchema)
  .max(50)
  .catch(() => []);

export const SidebarWidthCssSchema = z
  .string()
  .regex(/^\d+px$/)
  .nullable()
  .catch(null);

export const SIDEBAR_WIDTH_COOKIE = "sidebar_width";

export const SidebarStateCookieSchema = z
  .enum(["true", "false"])
  .catch("true")
  .transform((v) => v === "true");

export const AuthErrorParamSchema = z.enum(["domain_restricted", "auth_failed", "server_error"]);

export const PersistedExpansionSchema = z.object({
  state: z.record(z.string(), ExpansionMapSchema),
  version: z.number().int(),
});

export const PersistedDriveExpansionSchema = z.object({
  state: z.object({
    expanded: ExpansionMapSchema,
    initialized: z.boolean(),
  }),
  version: z.literal(1),
});
