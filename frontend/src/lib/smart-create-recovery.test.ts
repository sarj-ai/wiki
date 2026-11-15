import {
  clearSmartCreateReceipt,
  readSmartCreateReceipt,
  saveSmartCreateReceipt,
} from "./smart-create-recovery";

describe("Smart Create recovery receipt", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("preserves request identity by owner and scope and clears only the matching terminal request", () => {
    const key = "66666666-6666-4666-8666-666666666666";
    const receipt = { kind: "unconfirmed", key } as const;
    saveSmartCreateReceipt("owner", "folder", receipt);
    expect(readSmartCreateReceipt("owner", "folder")).toEqual(receipt);
    expect(readSmartCreateReceipt("other", "folder")).toBeNull();
    expect(readSmartCreateReceipt("owner", "other")).toBeNull();
    clearSmartCreateReceipt(
      "owner",
      "folder",
      "77777777-7777-4777-8777-777777777777",
    );
    expect(readSmartCreateReceipt("owner", "folder")).toEqual(receipt);
    clearSmartCreateReceipt("owner", "folder", key);
    expect(readSmartCreateReceipt("owner", "folder")).toBeNull();
  });

  it("rejects contradictory persisted receipts without deleting them", () => {
    const storage = "sarj-wiki:smart-create:owner:folder";
    const raw = JSON.stringify({
      kind: "unconfirmed",
      key: "66666666-6666-4666-8666-666666666666",
      taskId: "55555555-5555-4555-8555-555555555555",
    });
    window.sessionStorage.setItem(storage, raw);
    expect(() => readSmartCreateReceipt("owner", "folder")).toThrow();
    expect(window.sessionStorage.getItem(storage)).toBe(raw);
  });
});
