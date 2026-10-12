import { documentAliasError } from "./document-alias-error";

describe("documentAliasError", () => {
  it.each([
    ["", "Enter a short-link name."],
    ["Eng-onboarding", "Uppercase letters aren’t allowed; use lowercase only."],
    ["eng onboarding", "Use hyphens between words; spaces and underscores aren’t allowed."],
    ["ab-cd\n", "Use hyphens between words; spaces and underscores aren’t allowed."],
    ["ab-cd\r", "Use hyphens between words; spaces and underscores aren’t allowed."],
    ["ab-cd\u{2028}", "Use hyphens between words; spaces and underscores aren’t allowed."],
    ["eng_onboarding", "Use hyphens between words; spaces and underscores aren’t allowed."],
    ["eng-2026", "Use lowercase letters and hyphens only; numbers and symbols aren’t allowed."],
    ["-eng-onboarding", "Use one hyphen between words, with no hyphen at the beginning or end."],
    ["eng--onboarding", "Use one hyphen between words, with no hyphen at the beginning or end."],
    ["onboarding", "Use two or three words separated by hyphens."],
    ["a-onboarding", "Each word must contain 2–12 lowercase letters."],
    ["engineeringteam-onboarding", "Each word must contain 2–12 lowercase letters."],
    [
      "abcdefghijkl-abcdefghijkl-abcdefg",
      "Keep the complete short link to 32 characters or fewer.",
    ],
  ])("explains why %j is invalid", (value, expected) => {
    expect(documentAliasError(value)).toBe(expected);
  });

  it.each(["eng-onboarding", "people-org-chart", "ab-cd", "abcdefghijkl-ab"])(
    "accepts %s",
    (value) => {
      expect(documentAliasError(value)).toBeNull();
    },
  );

  it("keeps error precedence for inputs that violate several rules", () => {
    expect(documentAliasError("ENG ONBOARDING--1")).toBe(
      "Uppercase letters aren’t allowed; use lowercase only.",
    );
    expect(documentAliasError("ab-cd-ef-gh")).toBe("Use two or three words separated by hyphens.");
    expect(documentAliasError("abcdefghijkl-abcdefghijkl-abcdef")).toBeNull();
  });
});
