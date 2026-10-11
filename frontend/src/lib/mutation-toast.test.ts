import { toast } from "sonner";

import { runWithErrorToast } from "./mutation-toast";

vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

describe("mutation cancellation feedback", () => {
  beforeEach(() => vi.mocked(toast.error).mockClear());

  it("quietly settles a retired mutation without evaluating its error formatter", async () => {
    const fallback = vi.fn(() => "Formatted error");
    await runWithErrorToast(async () => {
      await Promise.reject(new DOMException("The mutation session was retired", "AbortError"));
    }, fallback);
    expect(toast.error).not.toHaveBeenCalled();
    expect(fallback).not.toHaveBeenCalled();
  });

  it("keeps current errors visible", async () => {
    await runWithErrorToast(async () => {
      await Promise.reject(new Error("Current move denied"));
    }, "Move failed");
    expect(toast.error).toHaveBeenCalledWith("Current move denied");
  });
});
