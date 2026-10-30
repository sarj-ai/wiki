import { z } from "zod";

export const TransformModeSchema = z.enum(["format", "unslop", "both"]);
