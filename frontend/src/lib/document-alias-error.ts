const MIN_WORDS = 2;
const MAX_WORDS = 3;
const MIN_WORD_LENGTH = 2;
const MAX_WORD_LENGTH = 12;
const MAX_ALIAS_LENGTH = 32;
const VALID_ALIAS = /^[a-z]{2,12}(?:-[a-z]{2,12}){1,2}(?![\s\S])/;

export function documentAliasError(value: string): null | string {
  if (value.length <= MAX_ALIAS_LENGTH && VALID_ALIAS.test(value)) return null;
  if (value.length === 0) return "Enter a short-link name.";
  if (/[A-Z]/.test(value)) return "Uppercase letters aren’t allowed; use lowercase only.";
  if (/[\s_]/.test(value))
    return "Use hyphens between words; spaces and underscores aren’t allowed.";
  if (/[^a-z-]/.test(value)) {
    return "Use lowercase letters and hyphens only; numbers and symbols aren’t allowed.";
  }
  if (value.startsWith("-") || value.endsWith("-") || value.includes("--")) {
    return "Use one hyphen between words, with no hyphen at the beginning or end.";
  }

  const words = value.split("-");
  if (words.length < MIN_WORDS || words.length > MAX_WORDS) {
    return "Use two or three words separated by hyphens.";
  }
  const invalidWord = words.find(
    (word) => word.length < MIN_WORD_LENGTH || word.length > MAX_WORD_LENGTH,
  );
  if (invalidWord !== undefined) return "Each word must contain 2–12 lowercase letters.";
  if (value.length > MAX_ALIAS_LENGTH)
    return "Keep the complete short link to 32 characters or fewer.";
  return null;
}
