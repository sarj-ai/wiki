import { describe, expect, it } from "vitest";

import { splitDocTabs, themedSrcdoc } from "@/components/wiki-document/doc-read-html";

const wrap = (css: string) =>
  `<html><head><style type="text/css">${css}</style></head><body></body></html>`;

describe("themedSrcdoc", () => {
  it("lightens the explicit black text Google exports put on every span", () => {
    expect(themedSrcdoc(wrap(".c0{color:#000000}"), true)).toContain("color:#ebebeb");
  });

  it("keeps already-light text untouched", () => {
    expect(themedSrcdoc(wrap(".c0{color:#e6e6e6}"), true)).toContain("color:#e6e6e6");
  });

  it("darkens white backgrounds, keeps dark ones", () => {
    const out = themedSrcdoc(
      wrap("td{background-color:#ffffff}.hdr{background-color:#123456}"),
      true,
    );
    expect(out).toContain("background-color:#2e2e2e");
    expect(out).toContain("background-color:#123456");
  });

  it("makes black table borders visible", () => {
    expect(themedSrcdoc(wrap("td{border:1pt solid #000000}"), true)).toContain(
      "border:1pt solid #616161",
    );
  });

  it("adds a sans fallback for fonts that are likely not installed", () => {
    expect(themedSrcdoc(wrap('.c0{font-family:"Proxima Nova"}'), true)).toContain(
      'font-family:"Proxima Nova",Arial,sans-serif',
    );
  });

  it("injects the classic dark design system (purple accents, Nunito, canvas)", () => {
    const out = themedSrcdoc(wrap(""), true);
    expect(out).toContain("color-scheme: dark");
    expect(out).toContain("#161618"); // classic dark canvas
    expect(out).toContain("#4b3483"); // signature purple secondary
    expect(out).toContain('font-family: "Nunito"');
    expect(out).toContain("/fonts/Nunito-Regular.ttf");
  });

  it("keeps Google's colors in light mode but paints the classic canvas + purple", () => {
    const out = themedSrcdoc(wrap(".c0{color:#000000}"), false);
    expect(out).toContain("color:#000000");
    expect(out).toContain("#f4f2ef"); // classic light canvas
    expect(out).toContain("#d0c3df"); // light purple secondary
  });

  it("keeps in-doc anchors in the same frame despite base target=_blank", () => {
    const out = themedSrcdoc(
      '<html><head></head><body><a class="c3" href="#h.abc">TOC</a><a href="https://x.com">ext</a></body></html>',
      true,
    );
    expect(out).toContain('<a target="_self" class="c3" href="#h.abc">');
    expect(out).toContain('<a href="https://x.com">');
  });

  it("uses continuous scroll layout (no paged paper sheets)", () => {
    const out = themedSrcdoc(wrap(""), true);
    expect(out).toContain("max-width: 64rem");
    expect(out).not.toContain("paged.polyfill.js");
    expect(out).not.toContain("@page { size: Letter");
  });

  it("repairs the literal '?' bullets Google exports emit", () => {
    expect(themedSrcdoc(wrap('.lst-a>li:before{content:"?  "}'), true)).toContain('content:"•  "');
  });

  it("promotes known status labels into classic pill badges", () => {
    const out = themedSrcdoc(
      "<html><head></head><body><td><span>Active</span></td><td><span>Deferred</span></td></body></html>",
      true,
    );
    expect(out).toContain('class="doc-status doc-status-active">Active</span>');
    expect(out).toContain('class="doc-status doc-status-warning">Deferred</span>');
  });

  it("forces purple-only link underlines (letter colors can stay multi-hue)", () => {
    const out = themedSrcdoc(wrap(""), true);
    expect(out).toContain("text-decoration-color: #4b3483");
    // Status letter colors stay multi-hue (Active blue, etc.)
    expect(out).toContain("#60a5fa");
  });
});

describe("splitDocTabs", () => {
  const tab = (name: string, body: string) =>
    `<p class="title" id="h.x" style="font-size:26pt"><span>${name}</span></p>${body}`;
  const doc = (inner: string) =>
    `<html><head><style>.a{}</style></head><body>${inner}</body></html>`;

  it("splits a tabbed export into one section per tab, decoding entities", () => {
    const html = doc(
      tab("&#129312; Principles", "<p>a</p>") + tab("Sales &amp; Roadmap", "<p>b</p>"),
    );
    const sections = splitDocTabs(html, "Fallback");
    expect(sections.map((s) => s.title)).toEqual(["🤠 Principles", "Sales & Roadmap"]);
    expect(sections[0]?.html).toContain("<p>a</p>");
    expect(sections[0]?.html).not.toContain("<p>b</p>");
    expect(sections[1]?.html).toContain("<p>b</p>");
    // every section stays a complete document with the shared styles
    expect(sections[1]?.html).toContain("<style>.a{}</style>");
    expect(sections[1]?.html).toContain("</body>");
  });

  it("returns a single fallback-titled section for tabless docs", () => {
    const sections = splitDocTabs(doc(tab("Only Title", "<p>a</p>")), "Doc name");
    expect(sections).toHaveLength(1);
    expect(sections[0]?.title).toBe("Doc name");
  });
});
