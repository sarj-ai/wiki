import {
  allowsIframeSpeculation,
  readNavigatorHints,
} from "./speculation-policy";

function hintedNavigator(
  deviceMemory: unknown,
  connection: unknown,
): Navigator {
  return new Proxy(navigator, {
    get(target, key, receiver) {
      if (key === "deviceMemory") return deviceMemory;
      if (key === "connection") return connection;
      const value: unknown = Reflect.get(target, key, receiver);
      return value;
    },
  });
}

describe("native browser capability boundaries", () => {
  it.each([NaN, Infinity, -Infinity, -0])(
    "preserves numeric device memory %s",
    (memory) => {
      const hints = readNavigatorHints(
        hintedNavigator(memory, { saveData: false }),
      );
      expect(Object.is(hints.deviceMemory, memory)).toBe(true);
      expect(allowsIframeSpeculation(hints)).toBe(memory > 2);
    },
  );

  it.each([null, undefined, "8", 8n])(
    "omits nonnumeric device memory %s",
    (memory) => {
      const hints = readNavigatorHints(hintedNavigator(memory, undefined));
      expect(Object.hasOwn(hints, "deviceMemory")).toBe(false);
      expect(allowsIframeSpeculation(hints)).toBe(true);
    },
  );

  it("reads only the named capability fields once and preserves independent valid hints", () => {
    const reads: string[] = [];
    const connection = {
      get effectiveType() {
        reads.push("effectiveType");
        return 7;
      },
      get saveData() {
        reads.push("saveData");
        return false;
      },
      get unused() {
        throw new Error("Unconsumed browser metadata must not be read");
      },
    };
    expect(readNavigatorHints(hintedNavigator(8, connection))).toEqual({
      connection: { saveData: false },
      deviceMemory: 8,
    });
    expect(reads).toEqual(["effectiveType", "saveData"]);
    expect(
      readNavigatorHints(
        hintedNavigator(8, Object.assign([], { saveData: true })),
      ),
    ).toEqual({
      connection: { saveData: true },
      deviceMemory: 8,
    });
  });
});
