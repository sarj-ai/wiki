import { z } from "zod";

const ConnectionValueSchema = z.union([z.object({}), z.custom<readonly unknown[]>(Array.isArray)]);

const DeviceMemorySchema = z.union([z.number(), z.nan(), z.literal([Infinity, -Infinity])]);
const EffectiveTypeSchema = z.string();
const SaveDataSchema = z.boolean();

interface NetworkHints {
  effectiveType?: string;
  saveData?: boolean;
}

export interface NavigatorHints {
  connection?: NetworkHints;
  deviceMemory?: number;
}

export function readNavigatorHints(value: Navigator): NavigatorHints {
  const connection: unknown = Reflect.get(value, "connection");
  const deviceMemory: unknown = Reflect.get(value, "deviceMemory");
  const effectiveType: unknown = readObjectValue(connection, "effectiveType");
  const saveData: unknown = readObjectValue(connection, "saveData");
  const parsedMemory = DeviceMemorySchema.safeParse(deviceMemory);
  const parsedType = EffectiveTypeSchema.safeParse(effectiveType);
  const parsedSaveData = SaveDataSchema.safeParse(saveData);
  return {
    ...(parsedMemory.success && { deviceMemory: parsedMemory.data }),
    ...((parsedType.success || parsedSaveData.success) && {
      connection: {
        ...(parsedType.success && { effectiveType: parsedType.data }),
        ...(parsedSaveData.success && { saveData: parsedSaveData.data }),
      },
    }),
  };
}

export function allowsReusableSpeculation(hints: NavigatorHints): boolean {
  return !hints.connection?.saveData;
}

export function allowsIframeSpeculation(hints: NavigatorHints): boolean {
  if (
    !allowsReusableSpeculation(hints) ||
    hints.connection?.effectiveType === "slow-2g" ||
    hints.connection?.effectiveType === "2g"
  ) {
    return false;
  }
  return hints.deviceMemory === undefined || hints.deviceMemory > 2;
}

function readObjectValue(value: unknown, key: string): unknown {
  return isBrowserConnectionValue(value) ? Reflect.get(value, key) : undefined;
}

function isBrowserConnectionValue(value: unknown): value is object {
  return ConnectionValueSchema.safeParse(value).success;
}
