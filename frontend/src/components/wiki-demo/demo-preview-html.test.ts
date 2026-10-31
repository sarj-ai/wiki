import { demoPreviewHtml } from "./demo-preview-html";

describe("embedded HTML section navigation", () => {
  it("keeps section links inside srcdoc while preserving text, citations and photo bytes", () => {
    const original =
      '<!doctype html><html><body><a href="#song">Song</a><a href="https://example.org/study" target="_blank" rel="noopener noreferrer">Study</a><img alt="Nightingale" src="data:image/jpeg;base64,unchanged"><h2 id="song">A song with an audience</h2><p>19 birds; 533 songs each.</p></body></html>';
    const before = new DOMParser().parseFromString(original, "text/html");
    const after = new DOMParser().parseFromString(demoPreviewHtml(original), "text/html");
    expect(after.querySelector('a[href="about:srcdoc#song"]')?.textContent).toBe("Song");
    expect(after.querySelector('a[href="https://example.org/study"]')?.outerHTML).toBe(
      before.querySelector('a[href="https://example.org/study"]')?.outerHTML,
    );
    expect(after.querySelector("img")?.outerHTML).toBe(before.querySelector("img")?.outerHTML);
    expect(after.body.textContent).toBe(before.body.textContent);
    expect(after.querySelector("#song")?.textContent).toBe("A song with an audience");
  });

  it("returns an unmodified document when it has no section links", () => {
    const html = "<!doctype html><html><body>Public sample</body></html>";
    expect(demoPreviewHtml(html)).toBe(html);
  });
});
