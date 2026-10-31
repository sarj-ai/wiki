export function demoPreviewHtml(html: string): string {
  const document = new DOMParser().parseFromString(html, "text/html");
  const anchors = document.querySelectorAll('a[href^="#"]');
  if (anchors.length === 0) return html;
  // srcdoc inherits the embedding page's base URL; keep section links inside this document.
  for (const anchor of anchors) {
    const fragment = anchor.getAttribute("href");
    if (fragment) anchor.setAttribute("href", `about:srcdoc${fragment}`);
  }
  return `<!doctype html>\n${document.documentElement.outerHTML}`;
}
