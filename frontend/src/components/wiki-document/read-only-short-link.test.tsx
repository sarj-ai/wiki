import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";

import { WIKI_READ_API } from "@/lib/wiki-read-api";

import { ReadOnlyShortLink } from "./read-only-short-link";

vi.mock("@/lib/wiki-read-api", () => ({
  WIKI_READ_API: { getDocumentAlias: vi.fn() },
}));

describe("ReadOnlyShortLink", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    flushSync(() => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("shows and copies an existing alias without mutation controls", async () => {
    vi.mocked(WIKI_READ_API.getDocumentAlias).mockResolvedValue({ alias: "people-org-chart" });
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();

    renderReader(root);

    const copyButton = await findButton(container, "Copy short link");
    expect(copyButton).toHaveTextContent("go/people-org-chart");
    expect(container).not.toHaveTextContent(/create|rename|remove|save changes/i);
    expect(container.querySelector("input")).not.toBeInTheDocument();

    copyButton.click();

    await vi.waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("http://localhost:3000/go/people-org-chart");
      expect(copyButton).toHaveAccessibleName("Short link copied");
    });
  });

  it("renders nothing when the document has no alias", async () => {
    vi.mocked(WIKI_READ_API.getDocumentAlias).mockResolvedValue({ alias: undefined });

    const { queryClient } = renderReader(root);

    await vi.waitFor(() => {
      expect(queryClient.getQueryState(["document-alias", "read-only-file"])?.status).toBe(
        "success",
      );
    });
    const [fileId, signal] = vi.mocked(WIKI_READ_API.getDocumentAlias).mock.calls[0] ?? [];
    expect(fileId).toBe("read-only-file");
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(container).toBeEmptyDOMElement();
  });
});

function renderReader(root: Root): { queryClient: QueryClient } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  flushSync(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ReadOnlyShortLink fileId="read-only-file" />
      </QueryClientProvider>,
    );
  });
  return { queryClient };
}

async function findButton(container: HTMLElement, name: string): Promise<HTMLButtonElement> {
  return vi.waitFor(() => {
    const button = [...container.querySelectorAll<HTMLButtonElement>("button")].find(
      (candidate) => candidate.getAttribute("aria-label") === name,
    );
    if (button === undefined) throw new Error(`Button not found: ${name}`);
    return button;
  });
}
