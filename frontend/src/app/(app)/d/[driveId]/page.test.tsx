import { isValidElement } from "react";

import FilePage from "@/app/(app)/d/[driveId]/page";

const MOCKS = vi.hoisted(() => ({
  getFile: vi.fn(),
  headers: vi.fn(),
}));

vi.mock("next/headers", () => ({ headers: MOCKS.headers }));
vi.mock("@/lib/server-api", () => ({ serverWikiApi: { getFile: MOCKS.getFile } }));

describe("FilePage metadata hydration", () => {
  beforeEach(() => {
    MOCKS.getFile.mockReset();
    MOCKS.headers.mockReset();
  });

  it("never repeats metadata work for an RSC navigation or prefetch", async () => {
    MOCKS.headers.mockResolvedValue(new Headers({ rsc: "1" }));

    const view = await renderPage();

    expect(MOCKS.getFile).not.toHaveBeenCalled();
    expect(dehydratedQueries(view)).toHaveLength(0);
  });

  it("opportunistically hydrates metadata for a direct request", async () => {
    const file = { id: "doc-1", mimeType: "application/vnd.google-apps.document", name: "Doc" };
    MOCKS.headers.mockResolvedValue(new Headers());
    MOCKS.getFile.mockResolvedValue(file);

    const view = await renderPage();

    expect(MOCKS.getFile).toHaveBeenCalledOnce();
    expect(MOCKS.getFile.mock.calls[0]?.[0]).toBe("doc-1");
    expect(MOCKS.getFile.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal);
    expect(dehydratedQueries(view)).toHaveLength(1);
  });
});

async function renderPage() {
  return await FilePage({
    params: Promise.resolve({ driveId: "doc-1" }),
    searchParams: Promise.resolve({}),
  });
}

function dehydratedQueries(view: Awaited<ReturnType<typeof renderPage>>): unknown[] {
  expect(isValidElement(view)).toBe(true);
  if (!isValidElement<{ state: { queries: unknown[] } }>(view)) return [];
  return view.props.state.queries;
}
