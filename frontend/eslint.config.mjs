import sarj from "@sarj/eslint-plugin";
import vitest from "@vitest/eslint-plugin";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import prettier from "eslint-config-prettier/flat";
import importX from "eslint-plugin-import-x";
import perfectionist from "eslint-plugin-perfectionist";
import sonarjs from "eslint-plugin-sonarjs";
import testingLibrary from "eslint-plugin-testing-library";
import unicorn from "eslint-plugin-unicorn";
import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Type-aware linting: turn on the typed rule sets across the project.
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  // Disables ESLint rules that conflict with Prettier. Must come last among
  // the broad config sets — scoped overrides below it still win.
  prettier,
  {
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      "@typescript-eslint/switch-exhaustiveness-check": "error",
      // strict-type-checked sets allowNumber:false, which fires on every
      // dimension/percent we interpolate into a className or inline style.
      // Numbers and booleans coerce predictably; nullish/any are still bad.
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true, allowBoolean: true },
      ],
      "no-console": ["warn", { allow: ["warn", "error"] }],
      eqeqeq: ["error", "always", { null: "ignore" }],
      "prefer-const": "error",
      "no-var": "error",
      "no-await-in-loop": "error",
      "array-callback-return": "error",
      "prefer-template": "error",
      "object-shorthand": ["error", "always"],
      // Five core rules called out in the rollout plan.
      "no-constant-binary-expression": "error",
      "no-promise-executor-return": "error",
      "require-atomic-updates": "error",
      "no-unreachable-loop": "error",
      "no-self-compare": "error",
    },
  },
  {
    plugins: { sonarjs },
    rules: {
      "sonarjs/no-identical-functions": "error",
      "sonarjs/no-identical-expressions": "error",
      "sonarjs/no-duplicate-string": ["error", { threshold: 5 }],
      "sonarjs/cognitive-complexity": ["error", 15],
      "sonarjs/no-collapsible-if": "error",
      "sonarjs/no-redundant-jump": "error",
      "sonarjs/no-use-of-empty-return-value": "error",
      "sonarjs/no-all-duplicated-branches": "error",
      "sonarjs/no-element-overwrite": "error",
      "sonarjs/prefer-immediate-return": "error",
    },
  },
  {
    plugins: { unicorn },
    rules: {
      "unicorn/prefer-node-protocol": "error",
      "unicorn/prefer-includes": "error",
      "unicorn/prefer-string-replace-all": "error",
      "unicorn/prefer-array-some": "error",
      "unicorn/prefer-array-flat": "error",
      "unicorn/prefer-array-flat-map": "error",
      "unicorn/error-message": "error",
      "unicorn/no-useless-undefined": "error",
      "unicorn/prefer-top-level-await": "error",
      "unicorn/throw-new-error": "error",
      "unicorn/no-instanceof-builtins": "error",
      // eslint-plugin-unicorn 65 extended filename-case to lint directory
      // segments; exempt Next.js dynamic route dirs (e.g. `[driveId]`), whose
      // bracketed camelCase names are a framework convention, not a style choice.
      "unicorn/filename-case": [
        "error",
        { cases: { kebabCase: true, pascalCase: true }, ignore: [String.raw`^\[.*\]$`] },
      ],
    },
  },
  {
    plugins: { perfectionist },
    rules: {
      "perfectionist/sort-imports": ["error", { type: "natural", order: "asc" }],
    },
  },
  {
    plugins: { "import-x": importX },
    settings: { "import-x/resolver": { typescript: true } },
    rules: {
      "import-x/no-cycle": "error",
      "import-x/no-self-import": "error",
    },
  },
  {
    rules: {
      "jsx-a11y/click-events-have-key-events": "warn",
      "jsx-a11y/no-static-element-interactions": "warn",
      "jsx-a11y/no-noninteractive-element-interactions": "warn",
      "jsx-a11y/label-has-associated-control": "warn",
    },
  },
  // Rule set sourced from the plugin so new @sarj rules are picked up
  // automatically; "warn" is the non-breaking floor, with the currently-clean
  // rules promoted to error below.
  {
    plugins: { "@sarj": sarj },
    rules: Object.fromEntries(Object.keys(sarj.rules).map((name) => [`@sarj/${name}`, "warn"])),
  },
  {
    rules: {
      "@sarj/no-client-side-data-fetching": "error",
      "@sarj/no-enum": "error",
      "@sarj/prefer-schema-for-api-payload": "error",
      "@sarj/prefer-server-actions": "error",
      "@sarj/require-assert-never": "error",
      "@sarj/require-zod-form-validation": "error",
      "@sarj/zod-naming-convention": "error",
    },
  },
  // Test-file-only configs.
  {
    files: ["**/*.test.{ts,tsx}"],
    plugins: { vitest },
    rules: vitest.configs.recommended.rules,
    settings: { vitest: { typecheck: true } },
  },
  {
    files: ["**/*.test.tsx"],
    ...testingLibrary.configs["flat/react"],
  },
  // shadcn primitives we copied in but don't own — relax stylistic rules
  // that would force us to fork upstream patterns.
  {
    files: ["src/components/ui/**"],
    rules: {
      "react-hooks/purity": "off",
      "@typescript-eslint/consistent-type-definitions": "off",
      "@typescript-eslint/no-confusing-void-expression": "off",
      "@typescript-eslint/no-unused-expressions": "off",
      "@typescript-eslint/prefer-nullish-coalescing": "off",
      "sonarjs/no-duplicate-string": "off",
    },
  },
  // Config files live outside the typed project — disable type-aware rules.
  {
    files: ["*.config.{ts,mjs,js}", "*.mjs"],
    extends: [tseslint.configs.disableTypeChecked],
  },
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "coverage/**",
    ".open-next/**",
    "next-env.d.ts",
    // Codegen output (openapi-ts) — regenerated, never hand-edited.
    "src/lib/generated/**",
  ]),
]);

export default eslintConfig;
