import { describe, expect, it } from "vitest";

import { sanitizeRedirectPath } from "./url-utils";

describe("sanitizeRedirectPath", () => {
  it("passes a normal absolute path through unchanged", () => {
    expect(sanitizeRedirectPath("/d/abc123")).toBe("/d/abc123");
    expect(sanitizeRedirectPath("/")).toBe("/");
  });

  it("rejects protocol-relative URLs (open-redirect vector)", () => {
    expect(sanitizeRedirectPath("//evil.com")).toBe("/");
    expect(sanitizeRedirectPath("//evil.com/path")).toBe("/");
  });

  it("rejects absolute URLs with a scheme", () => {
    expect(sanitizeRedirectPath("https://evil.com")).toBe("/");
    expect(sanitizeRedirectPath("http://evil.com/path")).toBe("/");
    expect(sanitizeRedirectPath("javascript://evil")).toBe("/");
  });

  it("rejects paths that don't start with a single forward slash", () => {
    expect(sanitizeRedirectPath("evil.com")).toBe("/");
    expect(sanitizeRedirectPath("relative/path")).toBe("/");
    expect(sanitizeRedirectPath("")).toBe("/");
  });

  it("rejects paths embedding a scheme later in the string", () => {
    // Defends against tricks like /redirect?next=https://evil
    expect(sanitizeRedirectPath("/redirect?next=https://evil")).toBe("/");
  });

  it("preserves query strings and fragments on safe paths", () => {
    expect(sanitizeRedirectPath("/d/abc?ref=email")).toBe("/d/abc?ref=email");
    expect(sanitizeRedirectPath("/page#section")).toBe("/page#section");
  });
});

describe("sanitizeRedirectPath backslash hardening", () => {
  it("rejects backslash paths that URL parsers would normalize to //", () => {
    expect(sanitizeRedirectPath("/\\evil.com")).toBe("/");
    expect(sanitizeRedirectPath("/d\\..\\evil")).toBe("/");
  });
});
