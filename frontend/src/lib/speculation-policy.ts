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
  return {
    ...(typeof deviceMemory === "number" && { deviceMemory }),
    ...((typeof effectiveType === "string" || typeof saveData === "boolean") && {
      connection: {
        ...(typeof effectiveType === "string" && { effectiveType }),
        ...(typeof saveData === "boolean" && { saveData }),
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
  return typeof value === "object" && value !== null ? Reflect.get(value, key) : undefined;
}
