// @vitest-environment happy-dom

import {
  initializeDefaultExpandedDrive,
  resetExpansionState,
  resolveDriveExpanded,
  setDriveExpanded,
} from "@/hooks/use-expanded-folders";
import { expandedDrivesStorageKey } from "@/lib/browser-storage";
import { PersistedDriveExpansionSchema } from "@/lib/storage-schemas";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

const USER_ID = "drive-expansion-user";

describe("explicit Drive expansion", () => {
  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
    resetExpansionState(USER_ID);
    window.localStorage.clear();
  });

  it("initializes the first Drive once and preserves an explicit all-collapsed choice", () => {
    initializeDefaultExpandedDrive(USER_ID, "drive-1");

    const initial = readPersistedState();
    expect(initial.state.expanded).toEqual({ "drive-1": true });
    expect(initial.state.initialized).toBe(true);

    setDriveExpanded(USER_ID, "drive-1", false);
    initializeDefaultExpandedDrive(USER_ID, "drive-1");

    const collapsed = readPersistedState();
    expect(collapsed.state.expanded).toEqual({});
    expect(collapsed.state.initialized).toBe(true);
    expect(resolveDriveExpanded({ expanded: false, initialized: true }, true)).toBe(false);
  });

  it("uses the supplied default only before explicit state is initialized", () => {
    expect(resolveDriveExpanded({ expanded: false, initialized: false }, true)).toBe(true);
    expect(resolveDriveExpanded({ expanded: false, initialized: false }, false)).toBe(false);
  });
});

function readPersistedState() {
  const raw = window.localStorage.getItem(expandedDrivesStorageKey(USER_ID));
  return PersistedDriveExpansionSchema.parse(JSON.parse(raw ?? "null"));
}
