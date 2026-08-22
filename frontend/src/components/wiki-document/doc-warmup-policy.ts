const WARM_COUNT = 1;

interface NetworkHints {
  effectiveType?: string;
  saveData?: boolean;
}

interface NavigatorHints {
  connection?: NetworkHints;
  deviceMemory?: number;
}

export function readNavigatorHints(value: Navigator): NavigatorHints {
  const connection: unknown = Reflect.get(value, "connection");
  const deviceMemory: unknown = Reflect.get(value, "deviceMemory");
  const effectiveType: unknown =
    typeof connection === "object" && connection !== null
      ? Reflect.get(connection, "effectiveType")
      : null;
  const saveData: unknown =
    typeof connection === "object" && connection !== null
      ? Reflect.get(connection, "saveData")
      : null;
  return {
    ...(typeof deviceMemory === "number" ? { deviceMemory } : {}),
    ...(typeof effectiveType === "string" || typeof saveData === "boolean"
      ? {
          connection: {
            ...(typeof effectiveType === "string" ? { effectiveType } : {}),
            ...(typeof saveData === "boolean" ? { saveData } : {}),
          },
        }
      : {}),
  };
}

export function warmCountFor(navigatorHints: NavigatorHints): number {
  const connection = navigatorHints.connection;
  if (connection?.saveData || connection?.effectiveType === "slow-2g") return 0;
  return WARM_COUNT;
}
