import { z } from "zod";

const ZExpansionMap = z.record(z.string(), z.literal(true));

export const ZLastRefreshMs = z.coerce.number().int().nonnegative().catch(0);

export const ZSidebarWidthCss = z
  .string()
  .regex(/^\d+px$/)
  .nullable()
  .catch(null);

export const ZSidebarStateCookie = z
  .enum(["true", "false"])
  .catch("true")
  .transform((v) => v === "true");

export const ZAuthErrorParam = z.enum(["domain_restricted", "auth_failed", "server_error"]);

export const ZPersistedExpansion = z.object({
  state: z.record(z.string(), ZExpansionMap),
  version: z.number().int(),
});
