import { describe, expect, it } from "vitest";

import { sanitizeRedirectPath } from "./sanitize-redirect-path";

describe("sanitizeRedirectPath", () => {
  it("passes a normal absolute path through unchanged", () => {
    expect(sanitizeRedirectPath("/d/abc123")).toBe("/d/abc123");
    expect(sanitizeRedirectPath("/")).toBe("/");
  });

  it("rejects protocol-relative URLs (open-redirect vector)", () => {
    expect(sanitizeRedirectPath("//evil.com")).toBe("/");
    expect(sanitizeRedirectPath("//evil.com/path")).toBe("/");
  });

  it.each(["https://evil.com", "https://evil.com/path", "javascript://evil"])(
    "rejects absolute URL %s",
    (path) => {
      expect(sanitizeRedirectPath(path)).toBe("/");
    },
  );

  it.each(["evil.com", "relative/path", ""])("rejects non-absolute path %s", (path) => {
    expect(sanitizeRedirectPath(path)).toBe("/");
  });

  it("rejects paths embedding a scheme later in the string", () => {
    expect(sanitizeRedirectPath("/redirect?next=https://evil")).toBe("/");
  });

  it("preserves query strings and fragments on safe paths", () => {
    expect(sanitizeRedirectPath("/d/abc?ref=email")).toBe("/d/abc?ref=email");
    expect(sanitizeRedirectPath("/page#section")).toBe("/page#section");
  });
});

describe("sanitizeRedirectPath backslash hardening", () => {
  it("rejects backslash paths that URL parsers would normalize to //", () => {
    expect(sanitizeRedirectPath(String.raw`/\evil.com`)).toBe("/");
    expect(sanitizeRedirectPath(String.raw`/d\..\evil`)).toBe("/");
  });
});
